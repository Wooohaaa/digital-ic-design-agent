"""Cross-check precise terminal exceptions for the RV32I core test EEI."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from pathlib import Path
import shutil
import subprocess

from .cpu_bench import TB_CPU, parse_and_compare, tool_environment
from .cpu_external import CORES, DATA_START, MEMORY_SIZE, _run, _write_json, load_engine, run_unicorn
from .evidence import sha
from .riscv import EBREAK, Program, enc_b, enc_i, enc_j, enc_s, run_reference


# Leave a visible store and two writebacks before every fault. A younger store
# must never be executed, including on a pipeline's speculatively fetched path.
PREFIX = (0x00001fb7, enc_i(0x13, 1, 0, 0, 0x5a), enc_s(2, 31, 1, 0))
SUFFIX = (enc_s(2, 31, 1, 8), EBREAK)


@dataclass(frozen=True)
class Fault:
    name: str
    instruction: int
    kind: str
    detail: tuple[int, ...]

    def program(self) -> Program:
        return Program(self.name, [*PREFIX, self.instruction, *SUFFIX])


def fault_cases() -> tuple[Fault, ...]:
    native = {
        'ecall': (0x00000073, 8),
        'invalid_opcode': (0xffffffff, 2),
        'invalid_slli': (enc_i(0x13, 1, 1, 0, 0x20), 2),
        'invalid_srli': (enc_i(0x13, 1, 5, 0, 0x20), 2),
        'invalid_jalr': (enc_i(0x67, 1, 1, 0, 0), 2),
        'invalid_load': (enc_i(0x03, 1, 3, 31, 0), 2),
        'invalid_store': (enc_s(3, 31, 1, 0), 2),
    }
    result = [Fault(name, instruction, 'native-exception', (cause,))
              for name, (instruction, cause) in native.items()]
    result += [
        Fault('rv32m_mul', 0x021080b3, 'unsupported-extension', (0x33,)),
        Fault('rvc_nop', 0x00000001, 'unsupported-extension', (0x01,)),
        Fault('zicsr_read', 0x300020f3, 'unsupported-extension', (0x73,)),
        Fault('zifencei', 0x0000100f, 'unsupported-extension', (0x0f,)),
    ]
    for mnemonic, funct3, size in [('lh', 1, 2), ('lhu', 5, 2), ('lw', 2, 4)]:
        result.extend(Fault(f'{mnemonic}_lane{lane}', enc_i(0x03, 3, funct3, 31, lane),
                            'unaligned-read', (DATA_START+lane, size))
                      for lane in range(1, 4) if lane % size)
    result += [Fault('lw_x0_lane2', enc_i(0x03, 0, 2, 31, 2), 'unaligned-read', (DATA_START+2, 4))]
    for mnemonic, funct3, size in [('sh', 1, 2), ('sw', 2, 4)]:
        result.extend(Fault(f'{mnemonic}_lane{lane}', enc_s(funct3, 31, 1, lane),
                            'unaligned-write', (DATA_START+lane, size))
                      for lane in range(1, 4) if lane % size)
    target = len(PREFIX)*4+2
    result += [Fault('jal_target', enc_j(3, 2), 'unaligned-target', (target,)),
               Fault('jalr_target_bit0', enc_i(0x67, 3, 0, 0, 3), 'unaligned-target', (2,))]
    for name, funct3, rs1, rs2 in [('beq', 0, 0, 0), ('bne', 1, 1, 0), ('blt', 4, 0, 1),
                                 ('bge', 5, 1, 0), ('bltu', 6, 0, 1), ('bgeu', 7, 1, 0)]:
        result.append(Fault(f'{name}_target', enc_b(funct3, rs1, rs2, 2),
                            'unaligned-target', (target,)))
    return tuple(result)


def fault_testbench() -> str:
    """Derive the same observable traces, with four stopped cycles observed."""
    old = ' if(halted===1\'b1) begin\n'
    new = """ if(stopped>0 && {halted,trap,rv}!==3'b110) $fatal(1,"trap not quiescent");
 if(halted===1'b1) stopped=stopped+1;
 if(stopped==4) begin
"""
    if TB_CPU.count(old) != 1 or TB_CPU.count('integer k, lane,') != 1:
        raise ValueError('The trusted retirement testbench layout changed')
    return TB_CPU.replace('integer k, lane,', 'integer stopped=0, k, lane,').replace(old, new)


def _observe_fault(fault: Fault, engine, registers) -> dict:
    """Let Unicorn execute the fault candidate and capture the policy input."""
    program = fault.program()
    model = engine.Uc(engine.UC_ARCH_RISCV, engine.UC_MODE_RISCV32)
    model.mem_map(0, MEMORY_SIZE)
    model.mem_write(0, b''.join(word.to_bytes(4, 'little') for word in program.words))
    pc = len(PREFIX)*4
    end = len(program.words)*4
    model.emu_start(0, pc, count=len(PREFIX))
    if model.reg_read(registers.UC_RISCV_REG_PC) != pc:
        raise ValueError(f'{fault.name}: did not reach fault PC')
    exceptions, accesses = [], []

    def interrupt(uc, cause, _):
        exceptions.append(cause)
        uc.emu_stop()

    def access(uc, kind, address, width, _value, _user):
        accesses.append((kind, address, width))
        if address % width:
            uc.emu_stop()

    model.hook_add(engine.UC_HOOK_INTR, interrupt)
    model.hook_add(engine.UC_HOOK_MEM_READ | engine.UC_HOOK_MEM_WRITE, access)
    model.emu_start(pc, end, count=1)
    next_pc = model.reg_read(registers.UC_RISCV_REG_PC) & 0xffffffff
    if fault.kind == 'native-exception':
        detail = tuple(exceptions)
    elif fault.kind == 'unaligned-target':
        detail = (next_pc,) if not exceptions and next_pc % 4 else ()
    elif fault.kind in ('unaligned-read', 'unaligned-write'):
        kind = engine.UC_MEM_READ if fault.kind == 'unaligned-read' else engine.UC_MEM_WRITE
        detail = tuple(value for operation, address, width in accesses if operation == kind and address % width
                       for value in (address, width))
    elif fault.kind == 'unsupported-extension':
        # The model has more extensions than this core's frozen ISA. The test
        # words are fixed above; support on the reference host is immaterial.
        detail = (fault.instruction & 0x7f,)
    else:
        raise ValueError(f'Unknown exception policy: {fault.kind}')
    if detail != fault.detail:
        raise ValueError(f'{fault.name}: expected {fault.kind} {fault.detail}, observed {detail}')
    return {'fault_pc': pc, 'next_pc': next_pc, 'native_exception': exceptions,
            'memory_accesses': accesses, 'evidence': fault.kind, 'detail': list(detail)}


def run_fault_matrix(sources: dict[str, Path], output: Path) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'fail', 'runner_sha256': sha(Path(__file__)), 'cases': {}, 'implementations': {}}
    try:
        engine, registers, report['oracle'] = load_engine()
        compiler, runtime, env = tool_environment()
        prefix = run_unicorn(Program('executed_prefix', [*PREFIX, EBREAK]), engine, registers)
        del prefix.trace[-1]   # Replace the prefix harness EBREAK with the fault under test.
        prefix.trap = True
        _write_json(output/'expected.json', {'trace': prefix.trace, 'stores': prefix.stores, 'memory': prefix.memory})
        (output/'tb_cpu.sv').write_text(fault_testbench(), encoding='utf-8')
        for fault in fault_cases():
            try:
                observation = _observe_fault(fault, engine, registers)
            except engine.UcError as exc:
                raise ValueError(f'{fault.name}: external engine could not observe the fault: {exc}') from exc
            program = fault.program()
            internal = run_reference(program)
            if (not internal.trap or internal.trace != prefix.trace or internal.stores != prefix.stores
                    or internal.memory != prefix.memory):
                raise ValueError(f'{fault.name}: internal oracle and external EEI disagree')
            image = program.words + [0]*(MEMORY_SIZE//4-len(program.words))
            path = output/f'{fault.name}.hex'
            path.write_text(''.join(f'{word:08x}\n' for word in image), encoding='ascii')
            report['cases'][fault.name] = {**asdict(fault), **observation, 'image_sha256': sha(path)}

        for name, source in sources.items():
            if name not in (*CORES, *(f'{core}-netlist' for core in CORES), 'mutant'):
                raise ValueError(f'Unknown implementation label: {name}')
            directory = output/name
            directory.mkdir()
            snapshot = directory/'rv32_core.v'
            shutil.copyfile(source, snapshot)
            result = {'rtl_sha256': sha(snapshot), 'cases': {}}
            report['implementations'][name] = result
            _run([compiler, '-g2012', '-s', 'tb_cpu', '-o', 'cpu.vvp', 'rv32_core.v', '../tb_cpu.sv'],
                 directory, env, directory/'compile.log', 120)
            for case in report['cases']:
                row = {'status': 'fail'}
                result['cases'][case] = row
                try:
                    actual = _run([runtime, 'cpu.vvp', f'+IMAGE=../{case}.hex', '+LIMIT=1000'],
                                  directory, env, directory/f'{case}.log', 20)
                    row.update(status='pass', **parse_and_compare(actual, prefix))
                except (AssertionError, ValueError, OSError, subprocess.SubprocessError) as exc:
                    row['error'] = str(exc)
            if sha(source) != result['rtl_sha256']:
                raise ValueError(f'{name}: source changed during test')
        if sources and all(row['status'] == 'pass' for item in report['implementations'].values()
                           for row in item['cases'].values()):
            report['status'] = 'pass'
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        report['error'] = str(exc)
    report['artifacts'] = {path.relative_to(output).as_posix(): sha(path)
                           for path in sorted(output.rglob('*')) if path.is_file()}
    _write_json(output/'report.json', report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sources = {core: Path.cwd()/f'benchmarks/rv32_{core}/rtl/rv32_core.v' for core in CORES}
    report = run_fault_matrix(sources, args.output)
    print(f"RV32I EEI faults: {report['status']} ({len(report['cases'])} cases)")
    return int(report['status'] != 'pass')


if __name__ == '__main__':
    raise SystemExit(main())
