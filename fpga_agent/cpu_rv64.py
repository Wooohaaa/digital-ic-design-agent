"""Reproduce RV64I base execution and a machine-trap boot slice."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import shutil
import subprocess

from .cpu_bench import tool_environment
from .cpu_external import _run, _write_json, load_engine
from .cpu_stage3 import EBREAK, MASK, PROJECT, RTL, _literal, encode, first_difference, make_programs
from .evidence import sha
from .lec import lec_valid, run_lec


TB = "tb/tb_rv64_base.sv"
RAM_START, RAM_END = 0x1000, 0x1400
WRITES_RD = {0x03, 0x13, 0x17, 0x1B, 0x33, 0x37, 0x3B, 0x67, 0x6F}


@dataclass(frozen=True)
class Case:
    name: str
    words: tuple[int, ...]
    fault_pc: int | None = None


def branch(funct3: int, rs1: int, rs2: int, offset: int) -> int:
    return (0x63 | funct3 << 12 | rs1 << 15 | rs2 << 20 | (offset >> 12 & 1) << 31
            | (offset >> 11 & 1) << 7 | (offset >> 5 & 63) << 25 | (offset >> 1 & 15) << 8)


def jal(rd: int, offset: int) -> int:
    return (0x6F | rd << 7 | (offset >> 20 & 1) << 31 | (offset >> 12 & 255) << 12
            | (offset >> 11 & 1) << 20 | (offset >> 1 & 1023) << 21)


def jalr(rd: int, rs1: int, offset: int) -> int:
    return 0x67 | rd << 7 | rs1 << 15 | (offset & 4095) << 20


def memory(funct3: int, rs1: int, register: int, offset: int, *, store: bool) -> int:
    common = funct3 << 12 | rs1 << 15
    if store:
        return 0x23 | common | register << 20 | (offset & 31) << 7 | (offset >> 5 & 127) << 25
    return 0x03 | common | register << 7 | (offset & 4095) << 20


def csr(funct3: int, rd: int, operand: int, address: int) -> int:
    return 0x73 | funct3 << 12 | rd << 7 | operand << 15 | address << 20


def base_cases(seeds: int) -> list[Case]:
    cases = []
    for program in make_programs(seeds):
        if program.name in ("load", "branch"):
            continue
        words = tuple(word for _, word in program.instructions)
        fault = None if program.terminator == EBREAK else 4 * len(words)
        cases.append(Case(program.name, words + (program.terminator, EBREAK), fault))

    for funct3, pairs in (
        (0, ((1, 1), (1, 2))), (1, ((1, 2), (1, 1))),
        (4, ((1, 2), (2, 1))), (5, ((2, 1), (1, 2))),
        (6, ((2, 1), (1, 2))), (7, ((1, 2), (2, 1))),
    ):
        cases.append(Case(f"branch-{funct3}", (
            encode("addi", 1, 0, -1), encode("addi", 2, 0, 1),
            branch(funct3, *pairs[0], 8), 0xFFFFFFFF,
            branch(funct3, *pairs[1], 8), encode("addi", 3, 0, funct3 + 1), EBREAK,
        )))
    cases.append(Case("not-taken-misaligned-target", (branch(1, 0, 0, 2), EBREAK)))
    cases.append(Case("jal-and-overlapping-jalr", (
        jal(4, 8), 0xFFFFFFFF, encode("addi", 3, 0, 21), jalr(3, 3, 0),
        0xFFFFFFFF, encode("add", 5, 3, 4), EBREAK,
    )))
    cases.append(Case("backward-loop", (
        encode("addi", 1, 0, 17), encode("addi", 2, 2, 3), encode("addi", 1, 1, -1),
        branch(1, 1, 0, -8), EBREAK,
    )))
    setup = [encode("lui", 10, operand=1)]
    setup.extend(word for _, word in _literal(11, 0x89ABCDEF80FF0181))
    for kind in range(4):
        width = 1 << kind
        for lane in range(0, 8, width):
            load_ops = (kind, kind + 4) if kind < 3 else (kind,)
            body = [memory(kind, 10, 11, lane, store=True), 0x0330000F]
            body.extend(memory(op, 10, 12, lane, store=False) for op in load_ops)
            body.extend((memory(kind, 10, 0, lane, store=False), EBREAK))
            cases.append(Case(f"lane-{width}-{lane}", tuple(setup + body)))
    cases.append(Case("negative-offset", (encode("lui", 10, operand=1), encode("addi", 10, 10, 8),
                                           memory(3, 10, 10, -8, store=True),
                                           memory(3, 10, 10, -8, store=False), EBREAK)))
    fault_words = [
        ("branch-align", branch(0, 0, 0, 2)), ("jal-align", jal(1, 2)),
        ("jalr-align", jalr(1, 0, 3)), ("bad-jalr", jalr(1, 0, 0) | 1 << 12),
        ("load-fault", memory(3, 0, 0, 0, store=False)),
        ("store-fault", memory(3, 0, 11, 8, store=True)),
        ("reserved-load", memory(7, 10, 0, 0, store=False)),
        ("reserved-store", memory(4, 10, 11, 0, store=True)),
        ("fence-i-unavailable", 0x0000100F),
    ]
    for kind in range(1, 4):
        fault_words.extend((("unaligned-load-" + str(kind), memory(kind, 10, 0, 1, store=False)),
                            ("unaligned-store-" + str(kind), memory(kind, 10, 11, 1, store=True))))
    prefix = [*setup, memory(3, 10, 11, 8, store=True)]
    for name, instruction in fault_words:
        cases.append(Case(name, tuple([*prefix, instruction, memory(3, 10, 11, 16, store=True), EBREAK]),
                          4 * len(prefix)))
    high = [word for _, word in _literal(10, 0x100001000)]
    cases.append(Case("high-address-must-not-alias", tuple(high + [memory(3, 10, 10, 0, store=True), EBREAK]),
                      4 * len(high)))
    cases.append(Case("fetch-fault", (jal(0, 16), EBREAK), 16))

    # A bounded workload exercises a dependency chain, store/load, and a backward branch.
    cases.append(Case("memory-sum-workload", (
        encode("lui", 10, operand=1), encode("addi", 1, 0, 64),
        memory(3, 10, 1, 0, store=True), memory(3, 10, 3, 0, store=False),
        encode("add", 4, 4, 3), encode("addi", 10, 10, 8), encode("addi", 1, 1, -1),
        branch(1, 1, 0, -20), memory(3, 10, 4, 0, store=True), EBREAK,
    )))
    return cases


def _retirement(pc: int, word: int, rd: int, value: int, next_pc: int) -> str:
    return f"R {pc:016x} {word:08x} {rd:02x} {value:016x} {next_pc:016x}"


def _halt(pc: int, fault: bool) -> list[str]:
    return [f"H {pc:016x} 1 {int(fault)} 0"] * 3


def _base_reference(case: Case, engine, registers) -> list[str]:
    uc = engine.Uc(engine.UC_ARCH_RISCV, engine.UC_MODE_RISCV64)
    uc.mem_map(0, 8192)
    uc.mem_write(0, b"".join(word.to_bytes(4, "little") for word in case.words))
    reg = [getattr(registers, f"UC_RISCV_REG_X{i}") for i in range(32)]
    trace = []

    def store(_uc, _access, address, width, value, _context):
        lane = address % 8
        if not RAM_START <= address <= RAM_END - width or address % width:
            raise ValueError(f"{case.name}: unexpected reference write at 0x{address:x}")
        enable = ((1 << width) - 1) << lane
        payload = (value & ((1 << (8 * width)) - 1)) << (lane * 8)
        trace.append(f"W {address-lane:016x} {enable:02x} {payload:016x}")

    uc.hook_add(engine.UC_HOOK_MEM_WRITE, store)
    pc = 0
    for _ in range(1024):
        if pc == case.fault_pc:
            trace.extend(_halt(pc, True))
            break
        if pc % 4 or not 0 <= pc < len(case.words) * 4:
            raise ValueError(f"{case.name}: unexpected reference PC 0x{pc:x}")
        word = case.words[pc // 4]
        if word == EBREAK:
            if case.fault_pc is not None:
                raise ValueError(f"{case.name}: fault site was not reached")
            trace.append(_retirement(pc, word, 0, 0, pc))
            trace.extend(_halt(pc, False))
            break
        try:
            uc.emu_start(pc, 0x2000, count=1)
        except engine.UcError as exc:
            raise ValueError(f"{case.name}: Unicorn failed at PC 0x{pc:x}: {exc}") from exc
        next_pc = uc.reg_read(registers.UC_RISCV_REG_PC) & MASK
        rd = word >> 7 & 31 if word & 127 in WRITES_RD else 0
        trace.append(_retirement(pc, word, rd, uc.reg_read(reg[rd]) & MASK, next_pc))
        pc = next_pc
    else:
        raise ValueError(f"{case.name}: reference exceeded the instruction budget")
    ram = uc.mem_read(RAM_START, RAM_END-RAM_START)
    for index in range(0, len(ram), 8):
        data = int.from_bytes(ram[index:index+8], "little")
        if data:
            trace.append(f"D {RAM_START+index:04x} {data:016x}")
    return trace


def machine_cases() -> list[tuple[Case, list[str]]]:
    tests = []
    for name, fault, cause, tval in (
        ("ecall", 0x00000073, 11, 0), ("illegal", 0xFFFFFFFF, 2, 0xFFFFFFFF),
        ("misaligned-jump", jal(9, 2), 0, 14),
        ("store-access", memory(3, 0, 1, 8, store=True), 7, 8),
    ):
        setup = (encode("addi", 1, 0, 64), csr(1, 0, 1, 0x305), csr(5, 0, 8, 0x300))
        resume = (encode("addi", 9, 9, 1), csr(2, 10, 0, 0x300), EBREAK)
        handler = (csr(2, 4, 0, 0x342), csr(2, 5, 0, 0x341), csr(2, 6, 0, 0x343),
                   csr(2, 7, 0, 0x300), encode("addi", 5, 5, 4), csr(1, 0, 5, 0x341),
                   csr(5, 11, 3, 0x340), csr(6, 12, 4, 0x340), csr(7, 13, 2, 0x340),
                   csr(3, 14, 1, 0x340), csr(2, 15, 0, 0x340), 0x30200073)
        words = setup + (fault,) + resume + (0,) * (16 - len(setup) - 1 - len(resume)) + handler
        expected = [_retirement(i*4, word, int(i == 0), 64 if i == 0 else 0, i*4+4)
                    for i, word in enumerate(setup)]
        expected.append("T 0000000000000040")
        results = ((4, cause), (5, 12), (6, tval), (7, 0x1880), (5, 16), (0, 0),
                   (11, 0), (12, 3), (13, 7), (14, 5), (15, 5), (0, 0))
        for index, (word, (rd, value)) in enumerate(zip(handler, results)):
            pc = 64 + index * 4
            expected.append(_retirement(pc, word, rd, value, 16 if word == 0x30200073 else pc + 4))
        expected.extend((_retirement(16, resume[0], 9, 1, 20),
                         _retirement(20, resume[1], 10, 0x1888, 24),
                         _retirement(24, EBREAK, 0, 0, 24), *_halt(24, False)))
        tests.append((Case(name, words), expected))
    return tests


def _simulate(output: Path, source: Path, tb: Path, cases: list[tuple[Case, list[str]]],
              *, machine: bool, netlist: bool = False) -> tuple[list[str], list[str]]:
    output.mkdir()
    payload, expected = [str(len(cases))], []
    for index, (case, trace) in enumerate(cases):
        payload.extend([f"{len(case.words)} 1024", *(f"{word:08x}" for word in case.words)])
        expected.extend([f"C {index}", *trace])
    (output / "programs.txt").write_text("\n".join(payload) + "\n", encoding="ascii")
    (output / "expected.log").write_text("\n".join(expected) + "\n", encoding="ascii")
    compiler, runtime, env = tool_environment()
    mode = ["-DNETLIST"] if netlist else [f"-Ptb_rv64_base.MACHINE={int(machine)}"]
    _run([compiler, "-g2012", *mode, "-s", "tb_rv64_base", "-o", "cpu.vvp", str(source), str(tb)],
         output, env, output / "compile.log", 120)
    actual = _run([runtime, "cpu.vvp"], output, env, output / "simulation.log", 180).splitlines()
    return expected, actual


def _accept(output: Path, project: Path, cases: list[tuple[Case, list[str]]], *, machine: bool, report: dict) -> None:
    rtl, tb = project / RTL, project / TB
    manifest = json.loads((project / "project.json").read_bytes())
    manifest["formal_equivalence"]["top_parameters"] = {"FULL_BASE": 1, "MACHINE": int(machine)}
    manifest["testbench_sources"] = [TB]
    _write_json(project / "project.json", manifest)
    expected, actual = _simulate(output / "rtl", rtl, tb, cases, machine=machine)
    if diff := first_difference(expected, actual):
        report["difference"] = diff
        raise ValueError(f"{output.name}: RTL mismatch: {diff}")
    report["rtl_sha256"] = sha(rtl)
    proof = run_lec(project, timeout=240)
    valid, reason = lec_valid(project, proof)
    report["proof"] = {"status": proof["status"], "method": proof["method"], "reason": proof["reason"]}
    if not valid:
        raise ValueError(f"{output.name}: LEC: {reason}: {proof['reason']}")
    generated = project / proof["run_directory"]
    netlist = generated / "netlist.v"
    gate = json.loads((generated / "gate.json").read_bytes())
    report["proof"].update(state_bits=proof["state_relation"]["state_bits"], netlist_sha256=sha(netlist),
                           generic_cells=len(gate["modules"][proof["proof_top"]]["cells"]))
    expected, actual = _simulate(output / "netlist", netlist, tb, cases, machine=machine, netlist=True)
    if diff := first_difference(expected, actual):
        report["difference"] = diff
        raise ValueError(f"{output.name}: netlist mismatch: {diff}")
    report.update(cases=len(cases), retirements=sum(line.startswith("R ") for line in expected),
                  stores=sum(line.startswith("W ") for line in expected),
                  terminal_traps=sum(case.fault_pc is not None for case, _ in cases),
                  handled_traps=sum(line.startswith("T ") for line in expected),
                  trace_sha256=sha(output / "netlist" / "simulation.log"))
    report["workloads"] = {
        case.name: {"cycles_to_stop": sum(line.startswith(("R ", "T ")) for line in trace),
                    "committed_stores": sum(line.startswith("W ") for line in trace),
                    "ram_signature": [line for line in trace if line.startswith("D 1200")]}
        for case, trace in cases if case.name.endswith("-workload")
    }


def run(root: Path, output: Path, seeds: int) -> dict:
    if type(seeds) is not int or not 1 <= seeds <= 128:
        raise ValueError("seeds must be between 1 and 128")
    output = output.resolve()
    output.mkdir(parents=True)
    report = {"status": "fail", "target_profile": "RVA23S64 1.0", "seeds": seeds, "configs": {},
              "runner_sha256": sha(Path(__file__))}
    try:
        engine, registers, report["oracle"] = load_engine()
        base = [(case, _base_reference(case, engine, registers)) for case in base_cases(seeds)]
        for name, cases, machine in (("rv64i", base, False), ("machine", machine_cases(), True)):
            directory = output / name
            project = directory / "project"
            for filename in ("project.json", RTL, TB):
                target = project / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / PROJECT / filename, target)
            row = report["configs"][name] = {}
            _accept(directory, project, cases, machine=machine, report=row)
        report["status"] = "pass"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report["error"] = str(exc)
    _write_json(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, choices=range(1, 129), default=32, metavar="1..128")
    args = parser.parse_args()
    report = run(Path.cwd(), args.output, args.seeds)
    print(f"Stage 3 RV64 base and machine runtime: {report['status']}; {args.output / 'report.json'}")
    if report.get("error"):
        print(report["error"])
    return int(report["status"] != "pass")


if __name__ == "__main__":
    raise SystemExit(main())
