"""Independent, instruction-level RV32I reference and reproducible program corpus.

This is a test EEI, not a privileged implementation: EBREAK ends a program;
ECALL, reserved encodings and unaligned accesses terminate with trap.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import random

MASK = 0xFFFFFFFF
EBREAK = 0x00100073


def sext(value: int, bits: int) -> int:
    value &= (1 << bits) - 1
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def signed(value: int) -> int:
    return sext(value, 32)


def enc_i(op: int, rd: int, f3: int, rs1: int, imm: int) -> int:
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op


def enc_r(rd: int, f3: int, rs1: int, rs2: int, f7: int = 0) -> int:
    return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | 0x33


def enc_s(f3: int, rs1: int, rs2: int, imm: int) -> int:
    return ((imm & 0xFE0) << 20) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | ((imm & 31) << 7) | 0x23


def enc_b(f3: int, rs1: int, rs2: int, offset: int) -> int:
    return (((offset >> 12) & 1) << 31) | (((offset >> 5) & 63) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (((offset >> 1) & 15) << 8) | (((offset >> 11) & 1) << 7) | 0x63


def enc_j(rd: int, offset: int) -> int:
    return (((offset >> 20) & 1) << 31) | (((offset >> 1) & 1023) << 21) | (((offset >> 11) & 1) << 20) | (((offset >> 12) & 255) << 12) | (rd << 7) | 0x6F


def li(rd: int, value: int) -> list[int]:
    value &= MASK
    return [(((value + 0x800) >> 12) & 0xFFFFF) << 12 | rd << 7 | 0x37, enc_i(0x13, rd, 0, rd, sext(value, 12))]


@dataclass
class Program:
    name: str
    words: list[int]
    max_steps: int = 3000


@dataclass
class ReferenceResult:
    trace: list[tuple[int, int, int, int]] = field(default_factory=list)
    stores: list[tuple[int, int, int]] = field(default_factory=list)
    memory: dict[int, int] = field(default_factory=dict)
    coverage: set[str] = field(default_factory=set)
    trap: bool = False
    registers: list[int] = field(default_factory=lambda: [0] * 32)


def run_reference(program: Program) -> ReferenceResult:
    memory = bytearray(16384)
    for index, word in enumerate(program.words):
        memory[index * 4:index * 4 + 4] = word.to_bytes(4, 'little')
    result = ReferenceResult()
    regs = result.registers
    pc = 0

    class Trap(Exception):
        pass

    def aligned(address: int, size: int) -> None:
        if address % size or address < 0 or address + size > len(memory):
            raise Trap()

    for _ in range(program.max_steps):
        try:
            aligned(pc, 4)
            insn = int.from_bytes(memory[pc:pc + 4], 'little')
            opcode, rd, f3, rs1, rs2, f7 = insn & 127, (insn >> 7) & 31, (insn >> 12) & 7, (insn >> 15) & 31, (insn >> 20) & 31, insn >> 25
            a, b = regs[rs1], regs[rs2]
            immediate = sext(insn >> 20, 12)
            next_pc, write_rd, value, mnemonic = (pc + 4) & MASK, 0, 0, ''
            stop = False
            if opcode in (0x37, 0x17):
                value = (insn & 0xFFFFF000) + (pc if opcode == 0x17 else 0)
                write_rd, mnemonic = rd, 'AUIPC' if opcode == 0x17 else 'LUI'
            elif opcode == 0x6F:
                imm = sext(((insn >> 31) << 20) | (((insn >> 12) & 255) << 12) | (((insn >> 20) & 1) << 11) | (((insn >> 21) & 1023) << 1), 21)
                next_pc = (pc + imm) & MASK
                aligned(next_pc, 4)
                write_rd, value, mnemonic = rd, pc + 4, 'JAL'
            elif opcode == 0x67:
                if f3 != 0:
                    raise Trap()
                next_pc = ((a + immediate) & MASK) & ~1
                aligned(next_pc, 4)
                write_rd, value, mnemonic = rd, pc + 4, 'JALR'
            elif opcode == 0x63:
                conditions = {0: a == b, 1: a != b, 4: signed(a) < signed(b), 5: signed(a) >= signed(b), 6: a < b, 7: a >= b}
                names = {0: 'BEQ', 1: 'BNE', 4: 'BLT', 5: 'BGE', 6: 'BLTU', 7: 'BGEU'}
                if f3 not in conditions:
                    raise Trap()
                mnemonic = names[f3]
                taken = conditions[f3]
                result.coverage.add(f'{mnemonic}:{"taken" if taken else "not_taken"}')
                if taken:
                    imm = sext(((insn >> 31) << 12) | (((insn >> 7) & 1) << 11) | (((insn >> 25) & 63) << 5) | (((insn >> 8) & 15) << 1), 13)
                    next_pc = (pc + imm) & MASK
                    aligned(next_pc, 4)
            elif opcode in (0x03, 0x23):
                if opcode == 0x23:
                    immediate = sext(((insn >> 25) << 5) | ((insn >> 7) & 31), 12)
                    if f3 not in (0, 1, 2):
                        raise Trap()
                elif f3 not in (0, 1, 2, 4, 5):
                    raise Trap()
                size = 1 << (f3 & 3)
                address = (a + immediate) & MASK
                aligned(address, size)
                if opcode == 0x03:
                    raw = int.from_bytes(memory[address:address + size], 'little')
                    value = sext(raw, size * 8) if f3 < 4 else raw
                    write_rd = rd
                    mnemonic = {0: 'LB', 1: 'LH', 2: 'LW', 4: 'LBU', 5: 'LHU'}[f3]
                else:
                    raw = b & ((1 << (size * 8)) - 1)
                    lane = address & 3
                    result.stores.append((address, ((1 << size) - 1) << lane, (raw << (lane * 8)) & MASK))
                    memory[address:address + size] = raw.to_bytes(size, 'little')
                    mnemonic = {0: 'SB', 1: 'SH', 2: 'SW'}[f3]
                result.coverage.add(f'{mnemonic}:lane{address & 3}')
            elif opcode in (0x13, 0x33):
                is_imm = opcode == 0x13
                rhs = (immediate & MASK) if is_imm else b
                shift = (rs2 if is_imm else b) & 31
                if f3 == 0:
                    if not is_imm and f7 not in (0, 0x20):
                        raise Trap()
                    subtract = not is_imm and f7 == 0x20
                    value = a - rhs if subtract else a + rhs
                    mnemonic = 'ADDI' if is_imm else ('SUB' if subtract else 'ADD')
                elif f3 == 1:
                    if f7 != 0:
                        raise Trap()
                    value, mnemonic = a << shift, 'SLLI' if is_imm else 'SLL'
                elif f3 in (2, 3, 4, 6, 7):
                    if not is_imm and f7 != 0:
                        raise Trap()
                    value = {2: int(signed(a) < signed(rhs)), 3: int(a < rhs), 4: a ^ rhs, 6: a | rhs, 7: a & rhs}[f3]
                    mnemonic = ({2:'SLTI',3:'SLTIU',4:'XORI',6:'ORI',7:'ANDI'} if is_imm else {2:'SLT',3:'SLTU',4:'XOR',6:'OR',7:'AND'})[f3]
                elif f3 == 5:
                    if f7 not in (0, 0x20):
                        raise Trap()
                    arith = f7 == 0x20
                    value = signed(a) >> shift if arith else a >> shift
                    mnemonic = ('SRA' if arith else 'SRL') + ('I' if is_imm else '')
                else:
                    raise Trap()
                write_rd = rd
            elif opcode == 0x0F and f3 == 0:
                mnemonic = 'FENCE'
            elif insn == EBREAK:
                mnemonic, stop = 'EBREAK', True
            else:
                raise Trap()
            value &= MASK
            if write_rd:
                regs[write_rd] = value
            else:
                value = 0
            regs[0] = 0
            result.trace.append((pc, insn, write_rd, value))
            result.coverage.add(mnemonic)
            pc = next_pc
            if stop:
                break
        except Trap:
            result.trap = True
            result.coverage.add('trap')
            break
    else:
        raise RuntimeError(f'reference did not halt: {program.name}')
    result.memory = {address: int.from_bytes(memory[address:address + 4], 'little') for address in range(0x1000, 0x1100, 4)}
    return result


def corpus(seeds: int = 64) -> list[Program]:
    programs: list[Program] = []
    words = li(1, 0x80000000) + li(2, 0xFFFFFFFF) + li(3, 31) + li(4, 1)
    for f3 in range(8):
        words += [enc_r(5 + f3, f3, 1, 3), enc_i(0x13, 15 + f3, f3, 2, 31 if f3 in (1, 5) else -2048)]
    words += [enc_r(23, 0, 1, 2, 0x20), enc_r(24, 5, 1, 3, 0x20), enc_i(0x13, 25, 5, 1, 0x41F), enc_i(0x17, 26, 0, 0, 0), enc_i(0x13, 0, 0, 0, 123), 0x0000000F, EBREAK]
    programs.append(Program('alu_edges', words))
    for f3 in (0, 1, 4, 5, 6, 7):
        for a, b in ((0, 0), (-1, 1), (1, -1), (0x80000000, 0)):
            words = li(1, a) + li(2, b) + [enc_b(f3, 1, 2, 8), enc_i(0x13, 3, 0, 3, 9), EBREAK]
            programs.append(Program(f'branch_{f3}_{a & MASK:08x}_{b & MASK:08x}', words))
    words = li(31, 0x1000) + li(1, 0x80FF7F01)
    words += [enc_s(2, 31, 1, 0)]
    for f3 in (0, 4):
        words += [enc_i(3, 2 + off + f3, f3, 31, off) for off in range(4)]
    for off in (0, 2):
        words += [enc_i(3, 10 + off, 1, 31, off), enc_i(3, 14 + off, 5, 31, off)]
    words += [enc_s(0, 31, 1, off + 4) for off in range(4)]
    words += [enc_s(1, 31, 1, 8), enc_s(1, 31, 1, 10), enc_i(3, 0, 2, 31, 0), enc_i(3, 20, 2, 31, 8), EBREAK]
    programs.append(Program('memory_lanes', words))
    programs.append(Program('backward_loop', [enc_i(0x13, 1, 0, 0, 12), enc_i(0x13, 2, 0, 2, 7), enc_i(0x13, 1, 0, 1, -1), enc_b(1, 1, 0, -8), EBREAK]))
    programs.append(Program('jal_jalr_flush', [enc_j(1, 8), enc_s(2, 0, 0, 0), enc_i(0x13, 2, 0, 0, 21), enc_i(0x67, 3, 0, 2, 0), enc_s(2, 0, 0, 0), enc_i(0x13, 4, 0, 3, -1), EBREAK]))
    for label, instruction in {
        'ecall': 0x00000073, 'invalid_opcode': 0xFFFFFFFF,
        'invalid_op': enc_r(1, 0, 0, 0, 1), 'invalid_slli': enc_i(0x13, 1, 1, 0, 0x020),
        'invalid_srli': enc_i(0x13, 1, 5, 0, 0x020), 'invalid_jalr': enc_i(0x67, 1, 1, 0, 0),
        'invalid_load': enc_i(3, 1, 3, 0, 0), 'invalid_store': enc_s(3, 0, 1, 0),
        'jal_unaligned': enc_j(1, 2), 'jalr_unaligned': enc_i(0x67, 1, 0, 0, 2),
        'branch_unaligned': enc_b(0, 0, 0, 2), 'lh_unaligned': enc_i(3, 1, 1, 0, 1),
        'lw_unaligned': enc_i(3, 1, 2, 0, 2), 'sh_unaligned': enc_s(1, 0, 1, 1), 'sw_unaligned': enc_s(2, 0, 1, 2),
    }.items():
        programs.append(Program(label, [instruction, EBREAK]))
    programs.append(Program('untaken_unaligned_branch', [enc_b(1, 0, 0, 2), EBREAK]))
    programs.append(Program('jalr_same_rs1_rd',[enc_i(0x13,1,0,0,17),enc_i(0x67,1,0,1,0),enc_s(2,0,0,0),enc_s(2,0,0,0),enc_i(0x13,2,0,1,0),EBREAK]))
    programs.append(Program('jal_negative_offset',[enc_j(1,12),enc_i(0x13,4,0,0,3),enc_j(0,12),enc_i(0x13,2,0,0,9),enc_j(3,-12),EBREAK]))
    programs.append(Program('negative_memory_offset',li(31,0x1040)+li(1,0x89abcdef)+[enc_s(2,31,1,-32),enc_i(3,2,2,31,-32),EBREAK]))
    programs.append(Program('auipc_wrap',[(0xfffff<<12)|(1<<7)|0x17,(0x80000<<12)|(2<<7)|0x17,EBREAK]))
    programs.append(Program('load_x0_still_traps',[enc_i(3,0,2,0,2),EBREAK]))
    # RV32I mandates base implementations treat reserved FENCE modes as normal
    # fences and ignore rs1/rd, including the FENCE.TSO configuration.
    programs.append(Program('fence_forward_compatibility',[0xfabf8f8f,0x8330000f,EBREAK]))
    for seed in range(seeds):
        rng = random.Random(seed)
        words = li(31, 0x1000)
        for rd in range(1, 31):
            words += li(rd, rng.getrandbits(32))
        for index in range(180):
            rd, rs1, rs2 = rng.randrange(31), rng.randrange(31), rng.randrange(31)
            kind = rng.randrange(8)
            if kind < 3:
                f3 = rng.randrange(8)
                f7 = rng.choice([0, 0x20]) if f3 in (0, 5) else 0
                words.append(enc_r(rd, f3, rs1, rs2, f7))
            elif kind < 5:
                f3 = rng.randrange(8)
                imm = rng.randrange(-2048, 2048)
                if f3 in (1, 5):
                    imm = rng.randrange(32) | (0x400 if f3 == 5 and rng.randrange(2) else 0)
                words.append(enc_i(0x13, rd, f3, rs1, imm))
            elif kind == 5:
                f3 = rng.choice([0, 1, 2])
                offset = rng.randrange(64) & ~((1 << f3) - 1)
                words.extend([enc_s(f3, 31, rs2, offset), enc_i(3, rd, rng.choice([f3, f3 + 4]) if f3 < 2 else 2, 31, offset)])
            elif kind == 6:
                words.extend([enc_b(rng.choice([0, 1, 4, 5, 6, 7]), rs1, rs2, 8), enc_i(0x13, rd, 0, rd, 1)])
            else:
                words.extend([enc_j(rd, 8), enc_s(2, 31, rs2, 252)])
        words.append(EBREAK)
        programs.append(Program(f'random_{seed:03}', words))
    return programs
