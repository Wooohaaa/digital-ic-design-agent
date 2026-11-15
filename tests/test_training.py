"""Tests for the RSI stage 6 host-side self-training loop (fpga_agent.training)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fpga_agent import training
from fpga_agent.cli import main, parser


class ForwardIntTests(unittest.TestCase):
    def test_wraparound_and_sign_bit_relu_match_reference(self) -> None:
        # Independent brute-force reference with explicit 32-bit wrap and
        # sign-bit ReLU.
        def reference(a, b):
            n = len(a)
            out = [[0] * n for _ in range(n)]
            for i in range(n):
                for j in range(n):
                    acc = 0
                    for k in range(n):
                        acc = (acc + a[i][k] * b[k][j]) % (1 << 32)
                    out[i][j] = 0 if acc >= (1 << 31) else acc
            return out

        a = [[0x7FFFFFFF, 1, 2, 3], [1, 2, 3, 4], [0, 0, 0, 1], [-1, -2, -3, -4]]
        b = [[1, 100, 3, -1], [0, 2, 4, 6], [7, 8, 9, 10], [-1, -1, -1, -1]]
        a = [[v % (1 << 32) for v in row] for row in a]
        b = [[v % (1 << 32) for v in row] for row in b]
        self.assertEqual(training.forward_int(a, b), reference(a, b))

    def test_relu_clamps_sign_bit_set_sums_to_zero(self) -> None:
        # 0x80000000 is the most-negative 32-bit pattern -> clamped to 0.
        a = [[0x80000000]]
        b = [[1]]
        self.assertEqual(training.forward_int(a, b), [[0]])

    def test_positive_sum_is_preserved(self) -> None:
        a = [[3]]
        b = [[4]]
        self.assertEqual(training.forward_int(a, b), [[12]])


class TrainingLoopTests(unittest.TestCase):
    def test_loss_decreases_and_report_passes(self) -> None:
        report = training.run_training(steps=150, lr=0.5, seed=3)
        self.assertEqual(report["status"], "pass")
        self.assertLess(report["final_loss"], report["initial_loss"])
        self.assertGreater(report["forward_match_percent"], 0.0)

    def test_training_is_deterministic_for_a_seed(self) -> None:
        first = training.run_training(steps=40, lr=0.5, seed=7)
        second = training.run_training(steps=40, lr=0.5, seed=7)
        self.assertEqual(first["weight_sha256"], second["weight_sha256"])
        self.assertEqual(first["loss_curve"], second["loss_curve"])

    def test_report_written_to_project(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            report = training.run_training(project, steps=40, lr=0.5, seed=1)
            path = project / "reports" / "training.json"
            self.assertTrue(path.is_file())
            written = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(written["weight_sha256"], report["weight_sha256"])
            self.assertEqual(written["kind"], "training")

    def test_quantize_rounds_to_fixed_point(self) -> None:
        self.assertEqual(training.quantize([[0.5, -0.25]], 4), [[2, -1]])


class TrainingCliTests(unittest.TestCase):
    def test_parser_exposes_training_command(self) -> None:
        args = parser().parse_args(["training", "some/project", "--steps", "25", "--seed", "9"])
        self.assertEqual(args.command, "training")
        self.assertEqual(args.steps, 25)
        self.assertEqual(args.seed, 9)

    def test_cli_runs_training_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            code = main(["training", str(project), "--steps", "40", "--seed", "1"])
            self.assertEqual(code, 0)
            self.assertTrue((project / "reports" / "training.json").is_file())


if __name__ == "__main__":
    unittest.main()
