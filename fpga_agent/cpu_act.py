"""Run pinned upstream RV32I architecture tests under the repository's core EEI."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess

from .cpu_bench import tool_environment
from .cpu_external import CORES, _run, _write_json, load_engine
from .evidence import sha
from .riscv import EBREAK


ASSETS = Path(__file__).with_name('templates')/'rv32-act'
UPSTREAM = 'riscv-test-suite'
CASES = f'{UPSTREAM}/rv32i_m/I/src'
DATA_BASE = 0x200000
RAM_SIZE = DATA_BASE + 0x10000
INSTRUCTION_LIMIT = 100_000


@dataclass(frozen=True)
class Layout:
    halt: int
    begin: int
    end: int
    text_end: int


def parse_layout(symbols: str, image: bytes) -> Layout:
    entries = {}
    for line in symbols.splitlines():
        columns = line.split()
        if len(columns) == 3:
            address, _, name = columns
            entries[name] = int(address, 16)
    try:
        layout = Layout(entries['rsi_halt'], entries['begin_signature'], entries['end_signature'],
                        entries['rsi_text_end'])
        entry = entries['rvtest_entry_point']
    except KeyError as exc:
        raise ValueError(f'Missing ELF symbol: {exc.args[0]}') from exc
    if (entry != 0 or layout.halt % 4 or layout.begin % 4 or layout.end % 4 or layout.text_end % 4
            or not 0 < layout.halt < layout.text_end <= DATA_BASE
            or not DATA_BASE <= layout.begin < layout.end <= len(image) <= RAM_SIZE
            or image[layout.halt:layout.halt+4] != struct.pack('<I', EBREAK)):
        raise ValueError('ELF layout does not fit the aligned ACT core EEI')
    return layout


def parse_signature(stdout: str, layout: Layout) -> tuple[list[int], int]:
    actual = {}
    stop = []
    for line in stdout.splitlines():
        if line.startswith('SIGNATURE '):
            if stop:
                raise ValueError('Signature word appeared after the stop record')
            match = re.fullmatch(r'SIGNATURE ([0-9a-fA-F]{8}) ([0-9a-fA-F]{8})', line)
            if match is None:
                raise ValueError(f'Unknown or malformed signature word: {line}')
            address, word = (int(field, 16) for field in match.groups())
            if address in actual:
                raise ValueError(f'Duplicate signature address 0x{address:08x}')
            actual[address] = word
        elif line.startswith('DONE '):
            match = re.fullmatch(r'DONE ([1-9][0-9]*)', line)
            if match is None:
                raise ValueError(f'Malformed stop record: {line}')
            stop.append(int(match[1]))
    addresses = range(layout.begin, layout.end, 4)
    if len(stop) != 1 or list(actual) != list(addresses):
        raise ValueError('Expected one legal stop and the complete ordered signature range')
    return list(actual.values()), stop[0]


def external_signature(engine, registers, image: bytes, layout: Layout) -> list[int]:
    model = engine.Uc(engine.UC_ARCH_RISCV, engine.UC_MODE_RISCV32)
    model.mem_map(0, RAM_SIZE)
    model.mem_write(0, image)
    model.mem_protect(0, DATA_BASE, engine.UC_PROT_READ | engine.UC_PROT_EXEC)
    model.mem_protect(DATA_BASE, RAM_SIZE-DATA_BASE, engine.UC_PROT_READ | engine.UC_PROT_WRITE)
    model.emu_start(0, layout.halt, count=INSTRUCTION_LIMIT)
    if model.reg_read(registers.UC_RISCV_REG_PC) != layout.halt:
        raise ValueError('External reference did not reach the ACT halt marker')
    signature = bytes(model.mem_read(layout.begin, layout.end-layout.begin))
    if signature == image[layout.begin:layout.end]:
        raise ValueError('External ACT execution left the entire initialized signature unchanged')
    return [word for (word,) in struct.iter_unpack('<I', signature)]


def _snapshot_suite(source: Path, output: Path, names: tuple[str, ...]) -> dict:
    locked = json.loads((ASSETS/'suite.json').read_bytes())
    selected = tuple(f'{CASES}/{name}.S' for name in names)
    for name in selected:
        if name not in locked['files']:
            raise ValueError(f'Case outside the pinned RV32I inventory: {name}')
    files = {}
    for name in (f'{UPSTREAM}/env/arch_test.h', f'{UPSTREAM}/env/encoding.h', *selected):
        path = source/name
        content = path.read_bytes().replace(b'\r\n', b'\n')
        if hashlib.sha256(content).hexdigest() != locked['files'][name]:
            raise ValueError(f'Upstream source differs from ACT {locked["revision"]}: {name}')
        copy = output/'upstream'/name
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(content)
        files[name] = sha(copy)
    return {'release': locked['release'], 'revision': locked['revision'], 'sources': files}


def _toolchain(clang: str) -> tuple[str, str, str]:
    compiler = shutil.which(clang)
    if compiler is None:
        raise ValueError(f'Clang unavailable: {clang}')
    path = Path(compiler)
    suffix = '.exe' if path.suffix.lower() == '.exe' else ''
    nm, objcopy, linker = (path.with_name(name+suffix) for name in ('llvm-nm', 'llvm-objcopy', 'ld.lld'))
    if not all(tool.is_file() for tool in (nm, objcopy, linker)):
        raise ValueError('Clang must have sibling ld.lld, llvm-nm and llvm-objcopy executables')
    return compiler, str(nm), str(objcopy)


def _build_case(out: Path, name: str, toolchain: tuple[str, str, str], env: dict) -> tuple[bytes, Layout]:
    compiler, nm, objcopy = toolchain
    directory = out/'cases'/name
    directory.mkdir(parents=True)
    _run([compiler, '--target=riscv32-unknown-elf', '-fuse-ld=lld', '-march=rv32i', '-mabi=ilp32',
          '-DXLEN=32', '-mno-relax', '-nostdlib',
          '-Wl,--no-relax,--build-id=none', '-T', str(out/'link.ld'), '-I', str(out),
          '-I', str(out/'upstream'/UPSTREAM/'env'), str(out/'upstream'/CASES/f'{name}.S'),
          '-o', 'test.elf'], directory, env, directory/'build.log', 60)
    symbols = _run([nm, '--defined-only', 'test.elf'], directory, env, directory/'symbols.log', 15)
    _run([objcopy, '-O', 'binary', 'test.elf', 'test.bin'], directory, env, directory/'objcopy.log', 15)
    image = (directory/'test.bin').read_bytes()
    layout = parse_layout(symbols, image)
    with (directory/'image.hex').open('w', encoding='ascii') as image_hex:
        for address, block in ((0, image[:layout.text_end]), (DATA_BASE, image[DATA_BASE:])):
            image_hex.write(f'@{address//4:x}\n')
            padded = block + b'\0'*(-len(block) % 4)
            image_hex.writelines(f'{word:08x}\n' for (word,) in struct.iter_unpack('<I', padded))
    return image, layout


def run_act_matrix(root: Path, suite: Path, output: Path, clang: str,
                   names: tuple[str, ...], cores: tuple[str, ...] = CORES) -> dict:
    root, suite, output = root.resolve(), suite.resolve(), output.resolve()
    if (not names or len(set(names)) != len(names) or not cores or len(set(cores)) != len(cores)
            or not set(cores) <= set(CORES) or any(not re.fullmatch(r'[a-z0-9-]+', name) for name in names)):
        raise ValueError('Expected a nonempty unique selection of pinned cases and supported cores')
    output.mkdir(parents=True, exist_ok=False)
    report = {'schema_version': 1, 'status': 'blocked', 'runner_sha256': sha(Path(__file__)),
              'scope': 'ACT RV32I aligned signature comparison under the non-privileged test EEI',
              'cores': {}, 'cases': {}}
    errors = (OSError, ValueError, RuntimeError, subprocess.SubprocessError)
    try:
        report['upstream'] = _snapshot_suite(suite, output, names)
        compiler, runtime, env = tool_environment()
        toolchain = _toolchain(clang)
        engine, registers, report['oracle'] = load_engine()
    except errors as exc:
        report['error'] = str(exc)
        _write_json(output/'report.json', report)
        return report
    report['status'] = 'fail'
    execution_errors = (*errors, engine.UcError)
    try:
        for asset in ('model_test.h', 'link.ld', 'tb_act.sv', 'suite.json'):
            shutil.copyfile(ASSETS/asset, output/asset)
        report['tools'] = {}
        for label, path, flag in (('clang', toolchain[0], '--version'), ('iverilog', compiler, '-V')):
            info = _run([path, flag], output, env, output/f'{label}-version.log', 20)
            report['tools'][label] = {'version': next(line for line in info.splitlines() if line.strip()),
                                      'sha256': sha(Path(path))}
        expected = {}
        for name in names:
            try:
                image, layout = _build_case(output, name, toolchain, env)
                signature = external_signature(engine, registers, image, layout)
                expected[name] = layout, signature
                report['cases'][name] = {'status': 'pass', 'halt': layout.halt, 'signature_base': layout.begin,
                                         'signature_words': len(signature)}
                _write_json(output/'cases'/name/'reference.json', {'words': signature})
            except execution_errors as exc:
                report['cases'][name] = {'status': 'fail', 'error': str(exc)}
        for core in cores:
            directory = output/core
            directory.mkdir()
            source = root/f'benchmarks/rv32_{core}/rtl/rv32_core.v'
            snapshot = directory/'rv32_core.v'
            shutil.copyfile(source, snapshot)
            result = {'rtl_sha256': sha(snapshot), 'cases': {}}
            report['cores'][core] = result
            _run([compiler, '-g2012', '-Wall', '-s', 'tb_act', '-o', 'cpu.vvp',
                  'rv32_core.v', '../tb_act.sv'], directory, env, directory/'compile.log', 120)
            for name, (layout, reference) in expected.items():
                row = {'status': 'fail'}
                result['cases'][name] = row
                try:
                    actual = _run([runtime, 'cpu.vvp', f'+IMAGE=../cases/{name}/image.hex',
                                   f'+BEGIN={layout.begin:08x}', f'+END={layout.end:08x}',
                                   f'+HALT={layout.halt:08x}'],
                                  directory, env, directory/f'{name}.log', 25)
                    signature, cycles = parse_signature(actual, layout)
                    mismatch = next((i for i, pair in enumerate(zip(reference, signature))
                                     if pair[0] != pair[1]), None)
                    if mismatch is not None:
                        address = layout.begin+4*mismatch
                        raise ValueError(f'Signature mismatch at 0x{address:08x}: '
                                         f'actual={signature[mismatch]:08x} expected={reference[mismatch]:08x}')
                    row.update(status='pass', signature_words=len(signature), cycles=cycles)
                except errors as exc:
                    row['error'] = str(exc)
            if sha(source) != result['rtl_sha256']:
                raise ValueError(f'{core} RTL changed during the benchmark')
        if (len(expected) == len(names)
                and all(row['status'] == 'pass' for result in report['cores'].values()
                        for row in result['cases'].values())):
            report['status'] = 'pass'
    except execution_errors as exc:
        report['error'] = str(exc)
    report['artifacts'] = {path.relative_to(output).as_posix(): sha(path)
                           for path in sorted(output.rglob('*')) if path.is_file()}
    _write_json(output/'report.json', report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, required=True, help='Extracted or cloned upstream ACT root')
    parser.add_argument('--clang', default='clang', help='Clang with RISC-V target and sibling LLVM tools')
    parser.add_argument('--output', type=Path, required=True, help='New result directory')
    parser.add_argument('--case', action='append', help='Select a named case; repeat for several')
    args = parser.parse_args()
    locked = json.loads((ASSETS/'suite.json').read_bytes())
    names = tuple(args.case or [Path(name).stem for name in locked['files'] if name.startswith(CASES+'/')])
    try:
        report = run_act_matrix(Path.cwd(), args.suite, args.output, args.clang, names)
    except (OSError, ValueError) as exc:
        parser.exit(2, f'{exc}\n')
    print(f"RV32I ACT signature matrix: {report['status']}; report: {args.output/'report.json'}")
    return {'pass': 0, 'fail': 1, 'blocked': 2}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
