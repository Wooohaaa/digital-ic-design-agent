"""Route the Stage 3 machine-runtime core as an out-of-context ECP5 baseline."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from .cpu_external import _run, _write_json
from .cpu_stage3 import PROJECT, RTL
from .evidence import sha


DEVICE = "LFE5U-85F"
PACKAGE = "CABGA381"
TARGET_MHZ = 20
SEED = 7


def run(root: Path, output: Path) -> dict:
    output = output.resolve()
    output.mkdir(parents=True)
    report = {"status": "fail", "device": DEVICE, "package": PACKAGE, "target_mhz": TARGET_MHZ,
              "seed": SEED, "scope": "ECP5 out-of-context, machine-runtime core",
              "parameters": {"FULL_BASE": 1, "MACHINE": 1}}
    try:
        yosys, nextpnr = shutil.which("yosys"), shutil.which("nextpnr-ecp5")
        if not yosys or not nextpnr:
            raise ValueError("Native Yosys and nextpnr-ecp5 must be on PATH")
        env = os.environ.copy()
        snapshot = shutil.copyfile(root / PROJECT / RTL, output / "core.v")
        report["rtl_sha256"] = sha(snapshot)
        _run([yosys, "-V"], output, env, output / "yosys-version.log", 20)
        _run([nextpnr, "--version"], output, env, output / "nextpnr-version.log", 20)
        (output / "synth.ys").write_text(
            "read_verilog core.v\nchparam -set FULL_BASE 1 -set MACHINE 1 rv64_integer\n"
            "synth_ecp5 -top rv64_integer -json synth.json\ncheck -assert\n", encoding="ascii"
        )
        _run([yosys, "-s", "synth.ys"], output, env, output / "synth.log", 360)
        _run([nextpnr, "--85k", "--package", PACKAGE, "--out-of-context", "--freq", str(TARGET_MHZ),
              "--seed", str(SEED), "--json", "synth.json", "--write", "routed.json", "--report", "timing.json"],
             output, env, output / "route.log", 600)
        result = json.loads((output / "timing.json").read_bytes())
        clocks = result.get("fmax", {})
        if set(clocks) != {"clk"} or not clocks["clk"]["achieved"] >= TARGET_MHZ:
            raise ValueError(f"Expected one declared clock and closed timing at {TARGET_MHZ} MHz: {clocks}")
        report.update(status="pass", clocks=clocks, utilization=result["utilization"],
                      artifacts={name: sha(output / name) for name in ("synth.json", "routed.json", "timing.json")})
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report["error"] = str(exc)
    _write_json(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(Path.cwd(), args.output)
    print(f"ECP5 out-of-context RV64 baseline: {report['status']}; {args.output / 'report.json'}")
    if report.get("error"):
        print(report["error"])
    return int(report["status"] != "pass")


if __name__ == "__main__":
    raise SystemExit(main())
