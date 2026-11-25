"""Run pinned historical RV64I ACT signatures on RTL and its proved netlist."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import struct
import subprocess

from .cpu_act import (
    ASSETS, DATA_BASE, Layout, _build_case, _compile_verilator, _snapshot_suite, _toolchain,
    external_signature, parse_layout, parse_signature,
)
from .cpu_bench import tool_environment
from .cpu_external import _run, _write_json, load_engine
from .cpu_stage3 import PROJECT, RTL
from .evidence import sha
from .lec import lec_valid


TB = "tb/tb_rv64_act.sv"


def _write_image(path: Path, data: bytes, width: int) -> None:
    code = {4: "<I", 8: "<Q"}[width]
    padded = data + b"\0" * (-len(data) % width)
    path.write_text("".join(f"{word:0{width*2}x}\n" for (word,) in struct.iter_unpack(code, padded)),
                    encoding="ascii")


def _check_signature(actual: list[int], reference: list[int], layout: Layout) -> None:
    for index, (dut, oracle) in enumerate(zip(actual, reference, strict=True)):
        if dut != oracle:
            raise ValueError(f"Signature mismatch at 0x{layout.begin + 4*index:08x}: {dut:08x} != {oracle:08x}")


def _compare_image(directory: Path, image: bytes, layout: Layout,
                   engine, registers, simulators: dict[str, list[str]], env: dict) -> tuple[list[int], dict]:
    _write_image(directory / "text.hex", image[:layout.text_end], 4)
    _write_image(directory / "data.hex", image[DATA_BASE:], 8)
    reference = external_signature(engine, registers, image, layout, xlen=64)
    results = {}
    for name, command in simulators.items():
        args = [*command, "+TEXT=text.hex", "+DATA=data.hex",
                f"+BEGIN={layout.begin:x}", f"+END={layout.end:x}", f"+HALT={layout.halt:x}",
                f"+TEXT_END={layout.text_end:x}"]
        actual, cycles = parse_signature(_run(args, directory, env, directory / f"{name}.log", 30), layout)
        _check_signature(actual, reference, layout)
        results[name] = {"cycles": cycles, "signature_words": len(actual)}
    return reference, results


def _build_firmware(root: Path, out: Path, tools: tuple[str, str, str], env: dict) -> tuple[bytes, Layout]:
    directory = out / "firmware"
    directory.mkdir()
    compiler, nm, objcopy = tools
    software = root / PROJECT / "software"
    for name in ("start.S", "checksum.c"):
        shutil.copyfile(software / name, directory / name)
    _run([compiler, "--target=riscv64-unknown-elf", "-fuse-ld=lld", "-march=rv64i", "-mabi=lp64",
          "-ffreestanding", "-fno-stack-protector", "-O2", "-mno-relax", "-nostdlib",
          "-Wl,--no-relax,--build-id=none", "-T", str(out / "link.ld"), "start.S", "checksum.c", "-o", "test.elf"],
         directory, env, directory / "build.log", 60)
    symbols = _run([nm, "--defined-only", "test.elf"], directory, env, directory / "symbols.log", 15)
    _run([objcopy, "-O", "binary", "test.elf", "test.bin"], directory, env, directory / "objcopy.log", 15)
    image = (directory / "test.bin").read_bytes()
    return image, parse_layout(symbols, image)


def run(root: Path, suite: Path, proof_project: Path, clang: str, output: Path) -> dict:
    output, proof_project = output.resolve(), proof_project.resolve()
    output.mkdir(parents=True)
    report = {"status": "fail", "scope": "ACT 2.7.4 RV64I signatures on a bounded core EEI", "cases": {}}
    errors = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)
    try:
        proof = json.loads((proof_project / "reports/lec.json").read_bytes())
        config = json.loads((proof_project / "project.json").read_bytes())
        if config["formal_equivalence"].get("top_parameters") != {"FULL_BASE": 1, "MACHINE": 0}:
            raise ValueError("Specify the accepted RV64I base proof project")
        valid, reason = lec_valid(proof_project, proof)
        if not valid:
            raise ValueError(f"RV64I proof is invalid: {reason}")
        rtl, netlist = proof_project / RTL, proof_project / proof["run_directory"] / "netlist.v"
        if sha(rtl) != sha(root / PROJECT / RTL):
            raise ValueError("Proved RV64I source differs from the repository RTL")
        report["rtl_sha256"], report["netlist_sha256"] = sha(rtl), sha(netlist)
        engine, registers, report["oracle"] = load_engine()
        inventory = json.loads((ASSETS.with_name("rv64-act") / "suite.json").read_bytes())
        names = tuple(Path(name).stem for name in inventory["files"] if name.endswith(".S"))
        report["upstream"] = _snapshot_suite(suite, output, names, xlen=64)
        for asset in ("link.ld", "model_test.h"):
            shutil.copyfile(ASSETS / asset, output / asset)
        tb = output / "tb.sv"
        shutil.copyfile(root / PROJECT / TB, tb)
        report["testbench_sha256"] = sha(tb)
        compiler, runtime, env = tool_environment()
        toolchain = _toolchain(clang)
        report["tools"] = {
            "clang": _run([toolchain[0], "--version"], output, env, output / "clang.log", 20).splitlines()[0],
            "iverilog": _run([compiler, "-V"], output, env, output / "iverilog.log", 20).splitlines()[0],
        }
        _run([compiler, "-g2012", "-s", "tb_rv64_act", "-o", "rtl.vvp", str(rtl), str(tb)],
             output, env, output / "rtl-compile.log", 120)
        compiled = output / "compiled"
        compiled.mkdir()
        simulators = {"rtl": [runtime, str(output / "rtl.vvp")],
                      "netlist": _compile_verilator(netlist, tb, "tb_rv64_act", compiled, env, defines=("NETLIST",))}
        report["simulators"] = {"rtl": "icarus", "netlist": "verilator"}
        firmware, layout = _build_firmware(root, output, toolchain, env)
        words, results = _compare_image(output / "firmware", firmware, layout, engine, registers, simulators, env)
        expected_checksum = sum((i << 32) | (i ^ 0x55) for i in range(64))
        if words != [expected_checksum & 0xFFFFFFFF, expected_checksum >> 32]:
            raise ValueError(f"Compiled C firmware computed the wrong checksum: {words}")
        report["firmware"] = {"checksum": f"{expected_checksum:016x}", **results}
        for case in names:
            record = report["cases"][case] = {"status": "fail"}
            directory = output / "cases" / case
            try:
                image, layout = _build_case(output, case, toolchain, env, xlen=64)
                _, results = _compare_image(directory, image, layout, engine, registers, simulators, env)
                record.update(status="pass", image_sha256=sha(directory / "test.bin"), **results)
            except (*errors, engine.UcError) as exc:
                record["error"] = str(exc)
        if all(row["status"] == "pass" for row in report["cases"].values()):
            report["status"] = "pass"
    except errors as exc:
        report["error"] = str(exc)
    _write_json(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--proof-project", type=Path, required=True)
    parser.add_argument("--clang", default="clang")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(Path.cwd(), args.suite, args.proof_project, args.clang, args.output)
    passed = sum(row["status"] == "pass" for row in report["cases"].values())
    print(f"Historical RV64I ACT: {report['status']} ({passed}/{len(report['cases'])}); {args.output / 'report.json'}")
    if report.get("error"):
        print(report["error"])
    return int(report["status"] != "pass")


if __name__ == "__main__":
    raise SystemExit(main())
