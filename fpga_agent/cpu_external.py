"""Opt-in RV32I retirement comparison using Unicorn's independent CPU engine."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import re
from importlib import import_module
from pathlib import Path
import subprocess
from typing import Any

from .cpu_bench import TB_CPU, parse_and_compare, tool_environment
from .evidence import sha
from .riscv import EBREAK, Program, ReferenceResult, corpus


ENGINE_VERSION = '2.1.4'
MEMORY_SIZE = 0x4000
DATA_START, DATA_END = 0x1000, 0x1100
CORES = ('single', 'multi', 'pipeline')
WRITES_REGISTER = {0x03, 0x13, 0x17, 0x33, 0x37, 0x67, 0x6F}
OTHER_OPCODES = {0x0F, 0x23, 0x63}
EEI_TRAP_CASES = {
    'ecall', 'invalid_opcode', 'invalid_op', 'invalid_slli', 'invalid_srli',
    'invalid_jalr', 'invalid_load', 'invalid_store', 'jal_unaligned', 'jalr_unaligned',
    'branch_unaligned', 'lh_unaligned', 'lw_unaligned', 'sh_unaligned', 'sw_unaligned',
    'load_x0_still_traps',
}


def load_engine() -> tuple[Any, Any, dict]:
    try:
        engine = import_module('unicorn')
        registers = import_module('unicorn.riscv_const')
        binding = import_module('unicorn.unicorn_py3.unicorn')
    except (ImportError, OSError) as exc:
        raise RuntimeError(f'Unicorn {ENGINE_VERSION} is required for the external ISA benchmark') from exc
    version = engine.uc_version()
    native = '.'.join(str((version[2] >> shift) & 0xff) for shift in (24, 16, 8))
    if engine.__version__ != ENGINE_VERSION or native != ENGINE_VERSION:
        raise RuntimeError(f'Expected Unicorn {ENGINE_VERSION}; found {engine.__version__} / {version}')
    library = Path(binding.uclib._name)
    return engine, registers, {
        'name': 'Unicorn Engine', 'version': ENGINE_VERSION, 'native_version': native,
        'library': library.name, 'sha256': sha(library), 'binding_sha256': sha(Path(binding.__file__)),
    }


def run_unicorn(program: Program, engine: Any, registers: Any) -> ReferenceResult:
    """Execute one instruction at a time; translate only the retirement format."""
    words = program.words
    text_end = 4 * len(words)
    if not words or words[-1] != EBREAK or text_end > DATA_START:
        raise ValueError(f'{program.name}: expected EBREAK-terminated program in the first page')
    model = engine.Uc(engine.UC_ARCH_RISCV, engine.UC_MODE_RISCV32)
    model.mem_map(0, MEMORY_SIZE)
    image = b''.join(word.to_bytes(4, 'little') for word in words)
    model.mem_write(0, image)
    model.mem_protect(0, DATA_START, engine.UC_PROT_READ | engine.UC_PROT_EXEC)
    model.mem_protect(DATA_START, MEMORY_SIZE-DATA_START, engine.UC_PROT_READ | engine.UC_PROT_WRITE)
    reg_ids = [getattr(registers, f'UC_RISCV_REG_X{index}') for index in range(32)]
    result = ReferenceResult()
    invalid_access = None

    def memory_access(uc, access, address, size, value, _user_data):
        nonlocal invalid_access
        if size not in (1, 2, 4) or address % size or not DATA_START <= address <= DATA_END-size:
            invalid_access = (address, size)
            uc.emu_stop()
            return
        if access == engine.UC_MEM_WRITE:
            lane = address & 3
            data = value & ((1 << (8 * size)) - 1)
            result.stores.append((address, ((1 << size) - 1) << lane, data << (8 * lane)))

    model.hook_add(engine.UC_HOOK_MEM_READ | engine.UC_HOOK_MEM_WRITE, memory_access)
    pc = 0
    for _ in range(program.max_steps):
        if pc & 3 or not 0 <= pc < text_end:
            raise ValueError(f'{program.name}: PC outside aligned text: 0x{pc:08x}')
        insn = words[pc // 4]
        if pc == text_end - 4:
            result.trace.append((pc, EBREAK, 0, 0))
            break
        opcode = insn & 0x7F
        if opcode not in WRITES_REGISTER | OTHER_OPCODES:
            raise ValueError(f'{program.name}: opcode outside the benchmark at 0x{pc:08x}: {insn:08x}')
        try:
            model.emu_start(pc, text_end, count=1)
        except engine.UcError as exc:
            raise ValueError(f'{program.name}: external execution failed at 0x{pc:08x}: {exc}') from exc
        if invalid_access is not None:
            raise ValueError(f'{program.name}: data access outside aligned fixture RAM: {invalid_access}')
        rd = (insn >> 7) & 31 if opcode in WRITES_REGISTER else 0
        data = model.reg_read(reg_ids[rd]) & 0xFFFFFFFF if rd else 0
        result.trace.append((pc, insn, rd, data))
        pc = model.reg_read(registers.UC_RISCV_REG_PC) & 0xFFFFFFFF
    else:
        raise ValueError(f'{program.name}: external model did not reach the termination marker')
    ram = model.mem_read(DATA_START, DATA_END-DATA_START)
    result.memory = {DATA_START+offset: int.from_bytes(ram[offset:offset+4], 'little')
                     for offset in range(0, len(ram), 4)}
    result.registers = [model.reg_read(reg) & 0xFFFFFFFF for reg in reg_ids]
    return result


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def _run(command: list[str], directory: Path, env: dict, log: Path, timeout: int) -> str:
    result = subprocess.run(command, cwd=directory, env=env, capture_output=True,
                            encoding='utf-8', errors='replace', timeout=timeout)
    output = result.stdout+result.stderr
    log.write_text(output, encoding='utf-8')
    if result.returncode:
        raise ValueError(f'{Path(command[0]).name} exited {result.returncode}; see {log}')
    return output


def run_matrix(root: Path, output: Path, seeds: int) -> dict:
    if type(seeds) is not int or not 0 <= seeds <= 1024:
        raise ValueError('seeds must be 0..1024')
    root, output = root.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'status': 'blocked', 'seeds': seeds,
              'scope': 'aligned RV32I core comparison, EBREAK fixture terminator',
              'excluded_eei_traps': sorted(EEI_TRAP_CASES), 'cores': {},
              'runner_sha256': sha(Path(__file__)),
              'testbench_adapter_sha256': sha(Path(__file__).with_name('cpu_bench.py')),
              'corpus_sha256': sha(Path(__file__).with_name('riscv.py'))}
    try:
        engine, registers, report['oracle'] = load_engine()
        compiler, runtime, env = tool_environment()
        snapshots = {}
        for name in CORES:
            project = root/f'benchmarks/rv32_{name}'
            for relative in ('rtl/rv32_core.v', 'tb/tb_cpu.v'):
                snapshots[project/relative] = (project/relative).read_bytes()
            if snapshots[project/'tb/tb_cpu.v'].decode('utf-8').replace('\r\n', '\n') != TB_CPU:
                raise ValueError(f'{name}: unrecognized retirement testbench')
    except (OSError, RuntimeError, ValueError) as exc:
        report['error'] = str(exc)
        _write_json(output/'report.json', report)
        return report

    report['sources'] = {path.relative_to(root).as_posix(): hashlib.sha256(content).hexdigest()
                         for path, content in snapshots.items()}
    report['status'] = 'fail'
    try:
        compiler_info = _run([compiler, '-V'], output, env, output/'iverilog-version.log', 20)
        report['iverilog'] = next(line for line in compiler_info.splitlines() if line.strip())
        programs = corpus(seeds)
        if not EEI_TRAP_CASES <= {program.name for program in programs}:
            raise ValueError('Expected the reviewed EEI trap-case inventory')
        references = {}
        inputs = output/'programs'
        inputs.mkdir()
        for program in programs:
            if program.name in EEI_TRAP_CASES:
                continue
            if not re.fullmatch(r'[a-z0-9_]+', program.name) or program.name in references:
                raise ValueError(f'Invalid or duplicate program name: {program.name}')
            expected = run_unicorn(program, engine, registers)
            directory = inputs/program.name
            directory.mkdir()
            words = program.words + [0]*(MEMORY_SIZE//4-len(program.words))
            (directory/'program.hex').write_text(''.join(f'{word:08x}\n' for word in words), encoding='ascii')
            expected_record = asdict(expected)
            del expected_record['coverage']
            _write_json(directory/'reference.json', expected_record)
            references[program.name] = (program, expected)

        report['programs'] = len(references)
        for core in CORES:
            directory = output/core
            directory.mkdir()
            project = root/f'benchmarks/rv32_{core}'
            (directory/'rv32_core.v').write_bytes(snapshots[project/'rtl/rv32_core.v'])
            (directory/'tb_cpu.v').write_bytes(snapshots[project/'tb/tb_cpu.v'])
            cases = []
            report['cores'][core] = cases
            _run([compiler, '-g2012', '-Wall', '-s', 'tb_cpu', '-o', 'cpu.vvp',
                  'rv32_core.v', 'tb_cpu.v'], directory, env, directory/'compile.log', 120)
            for name, (program, expected) in references.items():
                row = {'name': name, 'status': 'fail'}
                cases.append(row)
                image = (Path('..')/'programs'/name/'program.hex').as_posix()
                try:
                    actual = _run([runtime, 'cpu.vvp', f'+IMAGE={image}', f'+LIMIT={program.max_steps*8+20}'],
                                  directory, env, directory/f'{name}.log', 20)
                    row.update(status='pass', **parse_and_compare(actual, expected))
                except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as exc:
                    row['error'] = str(exc)
                    break
        if any(path.read_bytes() != expected for path, expected in snapshots.items()):
            raise ValueError('RTL or harness changed during the benchmark')
        if all(len(cases) == len(references) and all(row['status'] == 'pass' for row in cases)
               for cases in report['cores'].values()):
            report['status'] = 'pass'
    except (OSError, ValueError, subprocess.SubprocessError, engine.UcError) as exc:
        report['error'] = str(exc)
    report['artifacts'] = {path.relative_to(output).as_posix(): sha(path)
                           for path in sorted(output.rglob('*')) if path.is_file()}
    _write_json(output/'report.json', report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, default=64)
    parser.add_argument('--output', type=Path, required=True, help='New result directory')
    args = parser.parse_args()
    try:
        report = run_matrix(Path.cwd(), args.output, args.seeds)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'{exc}\n')
    print(f"RV32I external reference: {report['status']}; report: {args.output/'report.json'}")
    return {'pass': 0, 'fail': 1, 'blocked': 2}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
