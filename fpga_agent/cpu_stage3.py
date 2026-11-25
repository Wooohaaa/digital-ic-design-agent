"""Accept the Stage 3 RV64I integer milestone against Unicorn and generic LEC."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import random
import shutil
import subprocess

from .cpu_bench import tool_environment
from .cpu_external import _run, _write_json, load_engine
from .evidence import sha
from .lec import lec_valid, run_lec


PROJECT = "benchmarks/rv64_integer"
RTL = "rtl/rv64_integer.v"
TB = "tb/tb_rv64_integer.sv"
EBREAK = 0x00100073
MASK = (1 << 64) - 1
REGISTER_OPS = {
    "add": (0x33, 0, 0), "sub": (0x33, 0, 0x20), "sll": (0x33, 1, 0),
    "slt": (0x33, 2, 0), "sltu": (0x33, 3, 0), "xor": (0x33, 4, 0),
    "srl": (0x33, 5, 0), "sra": (0x33, 5, 0x20), "or": (0x33, 6, 0), "and": (0x33, 7, 0),
    "addw": (0x3B, 0, 0), "subw": (0x3B, 0, 0x20), "sllw": (0x3B, 1, 0),
    "srlw": (0x3B, 5, 0), "sraw": (0x3B, 5, 0x20),
}
IMMEDIATE_OPS = {
    "addi": (0x13, 0), "slti": (0x13, 2), "sltiu": (0x13, 3), "xori": (0x13, 4),
    "ori": (0x13, 6), "andi": (0x13, 7), "addiw": (0x1B, 0),
}
SHIFT_OPS = {
    "slli": (0x13, 1, 0, 64), "srli": (0x13, 5, 0, 64), "srai": (0x13, 5, 0x400, 64),
    "slliw": (0x1B, 1, 0, 32), "srliw": (0x1B, 5, 0, 32), "sraiw": (0x1B, 5, 0x400, 32),
}
UPPER_OPS = {"lui": 0x37, "auipc": 0x17}
OPS = tuple(REGISTER_OPS) + tuple(IMMEDIATE_OPS) + tuple(SHIFT_OPS) + tuple(UPPER_OPS)


def encode(op: str, rd: int, rs1: int = 0, operand: int = 0) -> int:
    fields = rd << 7 | rs1 << 15
    if op in REGISTER_OPS:
        opcode, funct3, funct7 = REGISTER_OPS[op]
        return fields | opcode | funct3 << 12 | operand << 20 | funct7 << 25
    if op in IMMEDIATE_OPS:
        opcode, funct3 = IMMEDIATE_OPS[op]
        return fields | opcode | funct3 << 12 | (operand & 0xFFF) << 20
    if op in SHIFT_OPS:
        opcode, funct3, prefix, _ = SHIFT_OPS[op]
        return fields | opcode | funct3 << 12 | (prefix | operand) << 20
    return rd << 7 | UPPER_OPS[op] | (operand & 0xFFFFF) << 12


@dataclass(frozen=True)
class Program:
    name: str
    instructions: tuple[tuple[str, int], ...]
    terminator: int = EBREAK


def _literal(rd: int, value: int) -> list[tuple[str, int]]:
    octets = value.to_bytes(8, "big")
    words = [("addi", encode("addi", rd, 0, octets[0]))]
    for octet in octets[1:]:
        words.extend((("slli", encode("slli", rd, rd, 8)), ("addi", encode("addi", rd, rd, octet))))
    return words


def make_programs(seeds: int) -> list[Program]:
    programs = []
    for index, (a, b) in enumerate((
        (0x8000000080000000, 0xFFFFFFFFFFFFFFFF),
        (0x7FFFFFFFFFFFFFFF, 1), (0x8000000000000000, 1),
        (0x00000000FFFFFFFF, 32), (0x000000007FFFFFFF, 63),
        (0x80000000FFFFFFFF, 0),
    )):
        words = [("add", encode("add", 31, 1, 2))] + _literal(1, a) + _literal(2, b)
        words.extend((op, encode(op, 3, 1, 2)) for op in REGISTER_OPS)
        for immediate in (-2048, -1, 0, 1, 2047):
            words.extend((op, encode(op, 3, 1, immediate)) for op in IMMEDIATE_OPS)
        for op, (_, _, _, limit) in SHIFT_OPS.items():
            amounts = sorted({0, 1, 31, limit - 1, limit // 2})
            words.extend((op, encode(op, 3, 1, s)) for s in amounts)
        for op in UPPER_OPS:
            words.extend((op, encode(op, 3, operand=upper)) for upper in (0, 0x7FFFF, 0x80000, 0xFFFFF))
        words.extend((("addi", encode("addi", 0, 1, 1)), ("add", encode("add", 1, 0, 0))))
        programs.append(Program(f"directed-{index}", tuple(words)))

    initializer = tuple(("addi", encode("addi", rd, 0, 17 * rd)) for rd in range(1, 32))
    readback = tuple(("add", encode("add", rd, rd, (rd + 1) % 32)) for rd in range(1, 32))
    programs.append(Program("all-registers", initializer + readback))
    programs.append(Program("all-registers-after-reset",
                            tuple(("add", encode("add", reg, reg, 0)) for reg in range(32))))

    for seed in range(seeds):
        rng = random.Random(seed)
        words = [("add", encode("add", 31, 1, 2))]
        for rd in (1, 2, 3):
            words.extend(_literal(rd, rng.getrandbits(64)))
        for _ in range(96):
            op = rng.choice(OPS)
            rd, rs1 = rng.randrange(4), rng.randrange(4)
            if op in REGISTER_OPS:
                operand = rng.randrange(4)
            elif op in IMMEDIATE_OPS:
                operand = rng.randrange(-2048, 2048)
            elif op in SHIFT_OPS:
                operand = rng.randrange(SHIFT_OPS[op][3])
            else:
                operand = rng.getrandbits(20)
            words.append((op, encode(op, rd, rs1, operand)))
        programs.append(Program(f"random-{seed}", tuple(words)))

    probe = (("addi", encode("addi", 1, 0, -1)),)
    for name, instruction in (
        ("ecall", 0x00000073), ("unallocated", 0xFFFFFFFF), ("compressed", 0x00000001),
        ("multiply", 0x020080B3), ("load", 0x00003083), ("branch", 0x00000063),
        ("word-shift-bit5", 0x0200909B), ("word-right-shift-bit5", 0x4200D09B),
        ("illegal-register-shift", 0x400090B3), ("illegal-word-logic", 0x0000F0BB),
        ("illegal-immediate-shift", 0x04009093),
    ):
        programs.append(Program(name, probe, instruction))
    return programs


def _stimulus_and_reference(programs: list[Program], output: Path, engine, registers) -> tuple[list[str], set[int]]:
    """Use Unicorn for every ordinary instruction; terminal policy belongs to the fixture."""
    fixture = [str(len(programs))]
    lines = []
    terminal_lines = set()
    pc_id = registers.UC_RISCV_REG_PC
    reg_ids = [getattr(registers, f"UC_RISCV_REG_X{i}") for i in range(32)]
    for index, program in enumerate(programs):
        words = [word for _, word in program.instructions]
        # A mutant that retires the first terminator can still finish normally.
        image = [*words, program.terminator, encode("addi", 1, 1, 1), EBREAK]
        fixture.extend([str(len(image)), *(f"{word:08x}" for word in image)])
        lines.extend((f"C {index}", "S 0000000000000000 0 0 0"))
        model = engine.Uc(engine.UC_ARCH_RISCV, engine.UC_MODE_RISCV64)
        model.mem_map(0, 4096)
        model.mem_write(0, b"".join(word.to_bytes(4, "little") for word in image))
        for seq, word in enumerate(words):
            pc = seq * 4
            try:
                model.emu_start(pc, len(image) * 4, count=1)
            except engine.UcError as exc:
                raise ValueError(f"{program.name}: reference rejected word {seq} (0x{word:08x}): {exc}") from exc
            if model.reg_read(pc_id) != pc + 4:
                raise ValueError(f"{program.name}: unexpected reference PC after word {seq}")
            rd = (word >> 7) & 31
            data = model.reg_read(reg_ids[rd]) & MASK
            lines.extend((f"R {pc:016x} {word:08x} {rd:02x} {data:016x}", f"S {pc+4:016x} 0 0 1"))
        pc = len(words) * 4
        stop = program.terminator == EBREAK
        # If an illegal word retires, its first extra line appears exactly here.
        if stop:
            lines.append(f"R {pc:016x} {EBREAK:08x} 00 0000000000000000")
        else:
            terminal_lines.add(len(lines))
        lines.append(f"S {pc:016x} 1 {int(not stop)} {int(stop)}")
        lines.extend([f"S {pc:016x} 1 {int(not stop)} 0"] * 3)
    (output / "programs.txt").write_text("\n".join(fixture) + "\n", encoding="ascii")
    (output / "expected.log").write_text("\n".join(lines) + "\n", encoding="ascii")
    return lines, terminal_lines


def _simulate(output: Path, name: str, source: Path, tb: Path) -> list[str]:
    directory = output / name
    directory.mkdir()
    shutil.copyfile(output / "programs.txt", directory / "programs.txt")
    compiler, runtime, env = tool_environment()
    _run([compiler, "-g2012", "-s", "tb_rv64_integer", "-o", "cpu.vvp", str(source), str(tb)],
         directory, env, directory / "compile.log", 120)
    result = _run([runtime, "cpu.vvp"], directory, env, directory / "simulation.log", 120)
    return result.splitlines()


def first_difference(expected: list[str], actual: list[str]) -> dict | None:
    for index, (left, right) in enumerate(zip(expected, actual)):
        if left != right:
            return {"line": index + 1, "expected": left, "actual": right}
    if len(expected) != len(actual):
        return {"line": min(len(expected), len(actual)) + 1,
                "expected_lines": len(expected), "actual_lines": len(actual)}
    return None


def run_stage3(root: Path, output: Path, seeds: int = 32) -> dict:
    if type(seeds) is not int or not 1 <= seeds <= 128:
        raise ValueError("seeds must be between 1 and 128")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    project = output / "project"
    report = {"status": "fail", "milestone": "Stage 3 RV64I integer execution",
              "profile_target": "RVA23S64 1.0", "scope": "RV64I integer computational subset",
              "runner_sha256": sha(Path(__file__)), "seeds": seeds, "simulation": {}, "negative_controls": {}}
    try:
        for name in ("project.json", RTL, TB):
            target = project / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / PROJECT / name, target)
        rtl, tb = project / RTL, project / TB
        report["inputs"] = {name: sha(project / name) for name in ("project.json", RTL, TB)}
        engine, registers, report["oracle"] = load_engine()
        programs = make_programs(seeds)
        expected, terminal_lines = _stimulus_and_reference(programs, output, engine, registers)
        coverage = Counter(op for program in programs for op, _ in program.instructions)
        if set(coverage) != set(OPS):
            raise ValueError("Missing integer instruction coverage")
        report["program_count"] = len(programs)
        report["halt_cases"] = sum(program.terminator == EBREAK for program in programs)
        report["trap_cases"] = len(programs) - report["halt_cases"]
        report["retirements"] = sum(coverage.values()) + report["halt_cases"]
        report["coverage"] = dict(sorted(coverage.items()))

        for name, source in (("rtl", rtl), ("netlist", None)):
            if source is None:
                proof = run_lec(project, timeout=240)
                valid, reason = lec_valid(project, proof)
                if not valid:
                    raise ValueError(f"LEC: {reason}: {proof['reason']}")
                generated = project / proof["run_directory"]
                source = generated / "netlist.v"
                gate = json.loads((generated / "gate.json").read_bytes())
                report["lec"] = {"method": proof["method"], "state_bits": proof["state_relation"]["state_bits"],
                                 "proof": "project/reports/lec.json", "tool": proof["tool_version"],
                                 "generic_cells": len(gate["modules"][proof["proof_top"]]["cells"])}
            diff = first_difference(expected, _simulate(output, name, source, tb))
            report["simulation"][name] = {"source_sha256": sha(source), "difference": diff}
            if diff:
                raise ValueError(f"{name}: retirement or terminal mismatch at line {diff['line']}")

        source = rtl.read_bytes()
        for name, before, after in (
            ("zero-extend-word", b"{{32{word_value[31]}}, word_value}", b"{32'b0, word_value}"),
            ("accept-reserved-word-shift", b"legal = normal_shift;",
             b"legal = normal_shift || (immediate && word_op && funct7 == 1);"),
        ):
            if source.count(before) != 1:
                raise ValueError(f"{name}: missing unique mutation anchor")
            mutant = output / f"{name}.v"
            mutant.write_bytes(source.replace(before, after))
            diff = first_difference(expected, _simulate(output, name, mutant, tb))
            if not diff or not diff.get("actual", "").startswith("R "):
                raise ValueError(f"{name}: no retirement counterexample")
            if name == "accept-reserved-word-shift":
                correct_counterexample = (diff["line"] - 1 in terminal_lines
                                          and diff["actual"].split()[2] == "0200909b")
            else:
                correct_counterexample = diff["actual"].split()[:-1] == diff["expected"].split()[:-1]
            if not correct_counterexample:
                raise ValueError(f"{name}: mismatch occurred outside the expected gate")
            report["negative_controls"][name] = diff
        report["status"] = "pass"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report["error"] = str(exc)
    evidence = [output / name for name in ("programs.txt", "expected.log", "project/reports/lec.json")]
    evidence.extend(output.glob("*/simulation.log"))
    report["artifacts"] = {path.relative_to(output).as_posix(): sha(path) for path in evidence if path.is_file()}
    _write_json(output / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, choices=range(1, 129), default=32, metavar="1..128")
    args = parser.parse_args()
    report = run_stage3(Path.cwd(), args.output, args.seeds)
    print(f"Stage 3 RV64I integer acceptance: {report['status']}; {args.output / 'report.json'}")
    if report.get("error"):
        print(report["error"])
    return int(report["status"] != "pass")


if __name__ == "__main__":
    raise SystemExit(main())
