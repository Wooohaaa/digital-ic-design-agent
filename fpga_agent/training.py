"""RSI roadmap stage 6: host-side self-training loop ("Agent trains its own model").

This is the software half of RSI stage 6. The hardware half is the
``tiny_train_gpu`` fixture (``examples/agent_validation/tiny_train_gpu``), a
GEMM + ReLU forward-pass compute core: ``C = ReLU(A * B)`` with ``DW``-bit
wraparound arithmetic. This module trains a tiny weight matrix on the host with
gradient descent, then runs the **same** GEMM + ReLU math (including the 32-bit
wraparound and sign-bit ReLU convention) that the RTL implements, so the trained
weights have a forward pass that the accelerator can execute.

Scope boundary (honest): this is a toy training loop, not a datacenter training
stack. It has a single small linear layer, no autograd framework, no FP8/BF16
mixed precision, no optimizer beyond plain SGD, no distributed or datacenter
scaling, and no silicon execution here (the RTL forward pass is verified
separately by the ``tiny_train_gpu`` bench). It demonstrates the closed loop:
the agent trains its own model and its own chip can run the forward pass.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from .terminal import write_json

TRAINING_VERSION = 1
MASK32 = 0xFFFFFFFF


def _wrap32(value: int) -> int:
    return value & MASK32


def forward_int(a: list[list[int]], b: list[list[int]]) -> list[list[int]]:
    """``C = ReLU(A * B)`` with 32-bit wraparound — identical to tiny_train_gpu RTL.

    ``a`` and ``b`` are square matrices (or rectangular with ``len(a)`` rows and
    ``len(a[0])`` columns for ``a``, ``len(b[0])`` columns for ``b``). The
    accumulator wraps modulo 2^32 and ReLU clamps a sign-bit-set (negative) sum
    to zero.
    """
    rows, inner = len(a), len(a[0])
    cols = len(b[0])
    out = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            acc = 0
            for k in range(inner):
                acc = _wrap32(acc + _wrap32(a[i][k] * b[k][j]))
            out[i][j] = 0 if (acc >> 31) else acc
    return out


def forward_float(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    """Float reference forward pass ``C = ReLU(A * B)`` used for training."""
    rows, inner = len(a), len(a[0])
    cols = len(b[0])
    out = [[0.0] * cols for _ in range(rows)]
    for i in range(rows):
        for j in range(cols):
            acc = 0.0
            for k in range(inner):
                acc += a[i][k] * b[k][j]
            out[i][j] = acc if acc > 0.0 else 0.0
    return out


def make_dataset(seed: int, n: int, samples: int) -> tuple[list[list[float]], list[list[float]], list[list[float]]]:
    """Deterministic synthetic task: ``Y = ReLU(X * W_true)`` in float."""
    rng = random.Random(seed)
    w_true = [[rng.uniform(-1.0, 1.0) for _ in range(n)] for _ in range(n)]
    x = [[rng.uniform(-1.0, 1.0) for _ in range(n)] for _ in range(samples)]
    y = forward_float(x, w_true)
    return x, y, w_true


def train(
    x: list[list[float]],
    y: list[list[float]],
    *,
    n: int,
    steps: int,
    lr: float,
    seed: int = 0,
) -> tuple[list[list[float]], list[float]]:
    """Plain SGD on a single ReLU linear layer; returns ``(weights, loss_curve)``.

    Loss is mean-squared error over the ReLU output. The ReLU derivative is the
    usual 0/1 subgradient. All arithmetic is host float; the chip forward pass is
    the integer version above.
    """
    rng = random.Random(seed + 1)
    w = [[rng.uniform(-0.05, 0.05) for _ in range(n)] for _ in range(n)]
    samples = len(x)
    history: list[float] = []
    for _ in range(steps):
        pre = [[sum(x[s][k] * w[k][j] for k in range(n)) for j in range(n)] for s in range(samples)]
        out = [[pre[s][j] if pre[s][j] > 0.0 else 0.0 for j in range(n)] for s in range(samples)]
        err = [[out[s][j] - y[s][j] for j in range(n)] for s in range(samples)]
        loss = sum(e * e for row in err for e in row) / (samples * n)
        history.append(loss)
        grad = [[0.0] * n for _ in range(n)]
        for s in range(samples):
            for k in range(n):
                xk = x[s][k]
                for j in range(n):
                    if pre[s][j] > 0.0:
                        grad[k][j] += 2.0 * err[s][j] * xk / (samples * n)
        for k in range(n):
            for j in range(n):
                w[k][j] -= lr * grad[k][j]
    return w, history


def quantize(values: list[list[float]], scale: int) -> list[list[int]]:
    """Round float weights to int32 after scaling (fixed-point quantization)."""
    return [[int(round(v * scale)) for v in row] for row in values]


def _sign_pattern(matrix: list[list[float]]) -> list[list[bool]]:
    return [[value > 1e-9 for value in row] for row in matrix]


def _hash_matrix(matrix: list[list[int]]) -> str:
    payload = json.dumps(matrix, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def run_training(
    project: Path | None = None,
    *,
    n: int = 4,
    samples: int = 8,
    steps: int = 200,
    lr: float = 0.5,
    seed: int = 1,
    scale: int = 4096,
) -> dict:
    """Train the tiny model, check it learned, and tie the forward pass to the RTL.

    Returns a report dict; writes ``reports/training.json`` when ``project`` is a
    directory. ``status`` is ``pass`` only when the loss actually decreased and the
    quantized integer forward pass agrees with the float reference on the ReLU
    positive/zero pattern.
    """
    x, y, _w_true = make_dataset(seed, n, samples)
    w, history = train(x, y, n=n, steps=steps, lr=lr, seed=seed)

    initial_loss = history[0] if history else float("nan")
    final_loss = history[-1] if history else float("nan")
    learned = final_loss < initial_loss * 0.9

    # Forward pass on the trained weights: float reference vs the integer GEMM+ReLU
    # the chip implements.
    float_out = forward_float(x, w)
    x_q = quantize(x, scale)
    w_q = quantize(w, scale)
    # Integer path scales by 2*scale (two fixed-point factors).
    int_out = forward_int(x_q, w_q)
    pos_int = _sign_pattern(int_out)
    pos_float = _sign_pattern(float_out)
    agree = 0
    total = len(x) * n
    for s in range(len(x)):
        for j in range(n):
            if pos_int[s][j] == pos_float[s][j]:
                agree += 1
    match_percent = 100.0 * agree / total if total else 0.0
    # The integer path is scaled by scale^2; compare float magnitudes after
    # descending them back to the same units.
    denom = float(scale) * float(scale)
    magnitude_error = 0.0
    counted = 0
    for s in range(len(x)):
        for j in range(n):
            if pos_float[s][j]:
                magnitude_error += abs(int_out[s][j] / denom - float_out[s][j])
                counted += 1
    mean_mag_error = magnitude_error / counted if counted else 0.0

    status = "pass" if (learned and match_percent >= 95.0) else "fail"
    reason = (
        f"SGD loss {initial_loss:.6g} -> {final_loss:.6g} "
        f"({'decreased' if learned else 'did not decrease'}); "
        f"quantized int32 GEMM+ReLU forward agrees with the float reference on "
        f"{match_percent:.1f}% of ReLU positive/zero entries "
        f"(mean magnitude error {mean_mag_error:.4g})."
    )
    report = {
        "kind": "training",
        "training_version": TRAINING_VERSION,
        "status": status,
        "seed": seed,
        "n": n,
        "samples": samples,
        "steps": steps,
        "learning_rate": lr,
        "quantization_scale": scale,
        "initial_loss": initial_loss,
        "final_loss": final_loss,
        "loss_reduction_percent": (
            100.0 * (initial_loss - final_loss) / initial_loss if initial_loss else 0.0
        ),
        "forward_match_percent": match_percent,
        "forward_mean_magnitude_error": mean_mag_error,
        "weight_sha256": _hash_matrix(w_q),
        "quantized_weights": w_q,
        "loss_curve": [round(value, 9) for value in history],
        "reason": reason,
    }
    if project is not None:
        directory = Path(project)
        if directory.is_dir():
            write_json(directory / "reports" / "training.json", report)
    return report
