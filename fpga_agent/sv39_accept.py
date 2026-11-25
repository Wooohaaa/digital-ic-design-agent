"""Replay the standalone Sv39/Svade page-table walker acceptance cases."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess

from .cpu_bench import tool_environment
from .cpu_external import _run, _write_json
from .cpu_stage3 import first_difference
from .evidence import sha
from .lec import lec_valid, run_lec


PROJECT = "benchmarks/sv39_walker"
RTL = "rtl/sv39_walker.v"
TB = "tb/tb_sv39.sv"
ROOT = 0x100
VA = 0x40403234
PREFIX = [(0x300 << 10 | 1, 0), (0x500 << 10 | 1, 0)]


def fixtures() -> tuple[list[str], list[str], list[str]]:
    vectors, expected, names = [], [], []

    def case(name, responses, fault=0, physical=0, *, va=VA, kind=0, user=0, sum_=0, mxr=0):
        names.append(name)
        vectors.append(f"{va:x} {ROOT:x} {kind:x} {user} {sum_} {mxr} {len(responses)}")
        page_indices = [(va >> (12 + 9*level)) & 511 for level in (2, 1, 0)]
        for index, (pte, bus_fault) in enumerate(responses):
            table = ROOT if index == 0 else (responses[index-1][0] >> 10)
            expected.append(f"P {(table << 12) + page_indices[index] * 8:014x}")
            vectors.append(f"{pte:x} {bus_fault}")
        expected.append(f"D {fault} {physical:014x}")

    case("4KiB read", [*PREFIX, (0xABC << 10 | 0x43, 0)], physical=0xABC234)
    case("2MiB write", [PREFIX[0], (0x600 << 10 | 0xC7, 0)], physical=0x603234, kind=1)
    case("1GiB execute", [(0x40000 << 10 | 0x49, 0)], physical=0x40403234, kind=2)
    case("negative canonical VA", [(0x80000 << 10 | 0x43, 0)], physical=0x80403234,
         va=0xFFFFFFC040403234)
    case("noncanonical", [], fault=1, va=0x8000000000000000)
    case("bad access type", [], fault=1, kind=3)
    case("PTE bus fault", [PREFIX[0], (0, 1)], fault=2)
    case("invalid PTE", [(0, 0)], fault=1)
    case("write without read", [(0xC5, 0)], fault=1, kind=1)
    case("reserved top bits", [(1 << 60 | 0x43, 0)], fault=1)
    case("nonleaf reserved A", [(0x300 << 10 | 0x41, 0)], fault=1)
    case("nonleaf reserved D", [(0x300 << 10 | 0x81, 0)], fault=1)
    case("nonleaf reserved U", [(0x300 << 10 | 0x11, 0)], fault=1)
    case("pointer at level zero", [*PREFIX, (0x900 << 10 | 1, 0)], fault=1)
    case("unaligned 1GiB leaf", [(0x40001 << 10 | 0x43, 0)], fault=1)
    case("unaligned 2MiB leaf", [PREFIX[0], (0x601 << 10 | 0x43, 0)], fault=1)
    for name, flags, kind, user, sum_, mxr, fault in (
        ("U read U page", 0x53, 0, 1, 0, 0, 0), ("U read S page", 0x43, 0, 1, 1, 0, 1),
        ("S read U without SUM", 0x53, 0, 0, 0, 0, 1), ("S read U with SUM", 0x53, 0, 0, 1, 0, 0),
        ("S execute U with SUM", 0x59, 2, 0, 1, 0, 1), ("U execute U page", 0x59, 2, 1, 0, 0, 0),
        ("MXR enables read", 0x49, 0, 0, 0, 1, 0), ("read X without MXR", 0x49, 0, 0, 0, 0, 1),
        ("Svade A", 0x03, 0, 0, 0, 0, 1), ("Svade D", 0x47, 1, 0, 0, 0, 1),
        ("read with D clear", 0x43, 0, 0, 0, 0, 0), ("permission forbids write", 0xC3, 1, 0, 0, 0, 1),
    ):
        case(name, [*PREFIX, (0xABC << 10 | flags, 0)], fault,
             0 if fault else 0xABC234, kind=kind, user=user, sum_=sum_, mxr=mxr)
    return names, [str(len(names)), *vectors], expected


def _simulate(directory: Path, rtl: Path, tb: Path, vectors: list[str]) -> list[str]:
    directory.mkdir()
    (directory / "vectors.txt").write_text("\n".join(vectors) + "\n", encoding="ascii")
    compiler, runtime, env = tool_environment()
    _run([compiler, "-g2012", "-s", "tb_sv39", "-o", "dut.vvp", str(rtl), str(tb)],
         directory, env, directory / "compile.log", 120)
    return _run([runtime, "dut.vvp"], directory, env, directory / "simulation.log", 120).splitlines()


def run(root: Path, output: Path) -> dict:
    output = output.resolve()
    output.mkdir(parents=True)
    report = {"status": "fail", "scope": "standalone Sv39 walk, Svade, no TLB or core integration"}
    try:
        project = output / "project"
        for name in ("project.json", RTL, TB):
            target = project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / PROJECT / name, target)
        names, vectors, expected = fixtures()
        (output / "expected.log").write_text("\n".join(expected) + "\n", encoding="ascii")
        report["cases"] = names
        rtl, tb = project / RTL, project / TB
        if diff := first_difference(expected, _simulate(output / "rtl", rtl, tb, vectors)):
            raise ValueError(f"RTL mismatch: {diff}")
        proof = run_lec(project, timeout=120)
        valid, reason = lec_valid(project, proof)
        if not valid:
            raise ValueError(f"LEC: {reason}: {proof['reason']}")
        netlist = project / proof["run_directory"] / "netlist.v"
        if diff := first_difference(expected, _simulate(output / "netlist", netlist, tb, vectors)):
            raise ValueError(f"netlist mismatch: {diff}")
        source = rtl.read_bytes()
        anchor = b"kind != 2 && allow_user_data"
        if source.count(anchor) != 1:
            raise ValueError("Missing Sv39 SUM mutation anchor")
        mutant = output / "mutant.v"
        mutant.write_bytes(source.replace(anchor, b"allow_user_data"))
        diff = first_difference(expected, _simulate(output / "mutation", mutant, tb, vectors))
        if not diff or diff.get("expected") != "D 1 00000000000000" or diff.get("actual") != "D 0 00000000abc234":
            raise ValueError(f"SUM mutation did not expose an S-mode fetch from a U page: {diff}")
        report.update(status="pass", lec_state_bits=proof["state_relation"]["state_bits"],
                      rtl_sha256=sha(rtl), netlist_sha256=sha(netlist), mutation=diff,
                      trace_sha256=sha(output / "netlist" / "simulation.log"))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report["error"] = str(exc)
    _write_json(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run(Path.cwd(), args.output)
    print(f"Sv39 walker: {report['status']}; {args.output / 'report.json'}")
    if report.get("error"):
        print(report["error"])
    return int(report["status"] != "pass")


if __name__ == "__main__":
    raise SystemExit(main())
