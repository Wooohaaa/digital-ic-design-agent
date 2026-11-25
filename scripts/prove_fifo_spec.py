"""Prove the shared two-slot FIFO against its independent ordered-list model."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from fpga_agent.evidence import sha
from fpga_agent.lec import find_yosys
from fpga_agent.sv_conformance import default_suite


ROOT = Path(__file__).resolve().parents[1]
DUT = default_suite() / 'rtl_fifo/fifo_two.sv'
SPEC = ROOT / 'examples/formal/fifo_two_spec.sv'
PREPARE = r"""read_verilog -sv dut.sv
hierarchy -check -top fifo_two
proc
memory_map
opt_clean
cd fifo_two
rename \storage[0] slot0
rename \storage[1] slot1
cd ..
expose fifo_two/w:used fifo_two/w:read_index fifo_two/w:write_index fifo_two/w:slot0 fifo_two/w:slot1
read_verilog -sv spec.sv
hierarchy -check -top fifo_two_spec
proc
flatten
opt
check -assert
write_json model.json
write_rtlil model.il
"""
PROOFS = {
    'reset_base': '-seq 2 -set-at 1 reset_n 0 -prove-skip 1 -prove relation_ok 1 -prove ports_ok 1',
    'preservation': '-seq 2 -set-at 1 relation_ok 1 -prove-skip 1 -prove relation_ok 1',
    'observation': '-seq 1 -set-at 1 relation_ok 1 -prove ports_ok 1',
}
MUTANTS = {
    'reset_loses_to_handshake': ('if (!reset_n) begin', 'if (!reset_n && !push) begin', 'reset_base'),
    'full_write_bypass': ('push=in_valid && in_ready', 'push=in_valid && (in_ready || out_ready)', 'preservation'),
    'wrong_head_slot': ('storage[read_index]', 'storage[~read_index]', 'observation'),
}


def validate_model(path: Path) -> None:
    """SAT unrolls one edge per step; reject an incompatible clock or hidden assumptions."""
    module, = json.loads(path.read_text(encoding='utf-8'))['modules'].values()
    ports = module['ports']
    inputs = {name: len(port['bits']) for name, port in ports.items() if port['direction'] == 'input'}
    if inputs != {'clk': 1, 'reset_n': 1, 'in_valid': 1, 'out_ready': 1, 'in_word': 16}:
        raise ValueError('Unexpected formal input interface')
    clock = ports['clk']['bits']
    for cell in module['cells'].values():
        connections = cell['connections']
        if cell['type'] in {'$dff', '$dffe', '$sdff', '$sdffe', '$sdffce'}:
            if connections['CLK'] != clock or int(cell['parameters']['CLK_POLARITY'], 2) != 1:
                raise ValueError('Expected registers on the sole rising-edge clock')
        elif (cell['type'] not in {'$add', '$sub', '$and', '$or', '$xor', '$not', '$mux', '$pmux',
                                  '$logic_and', '$logic_or', '$logic_not', '$eq', '$eqx', '$ne',
                                  '$reduce_bool', '$reduce_and', '$lt', '$le', '$scopeinfo'}
              or any(set(bits) & set(clock) for bits in connections.values())):
            raise ValueError(f'Unsupported cell or clock use: {cell["type"]}')
    if any('init' in wire['attributes'] for wire in module['netnames'].values()):
        raise ValueError('Expected arbitrary initial register contents')


def prove(run: Path, tool: str, timeout: int, negative_stage: str | None = None) -> dict:
    def command(stage: str, source: str) -> tuple[int, str]:
        script = run / f'{stage}.ys'
        script.write_text(source, encoding='ascii')
        result = subprocess.run([tool, '-Q', '-T', '-s', script.name], cwd=run,
                                capture_output=True, encoding='utf-8', errors='replace',
                                check=False, timeout=timeout)
        log = result.stdout + result.stderr
        (run / f'{stage}.log').write_text(log, encoding='utf-8')
        return result.returncode, log

    code, _ = command('prepare', PREPARE)
    if code:
        raise ValueError(f'Formal model preparation failed: {run / "prepare.log"}')
    validate_model(run / 'model.json')
    result = {}
    for stage, options in PROOFS.items():
        witness = run / f'{stage}-cex.json'
        code, log = command(stage, f'read_rtlil model.il\nsat {options} -verify -show-ports -dump_json {witness.name}\n')
        if stage == negative_stage:
            if code == 0 or 'model found: FAIL!' not in log or not witness.is_file():
                raise ValueError(f'No SAT counterexample for {stage}: {run}')
            result[stage] = {'status': 'counterexample', 'sha256': sha(witness)}
            break
        if code != 0 or 'no model found: SUCCESS!' not in log:
            raise ValueError(f'{stage} not proven: {run / (stage + ".log")}')
        result[stage] = {'status': 'proven'}
    if negative_stage is not None:
        # An induction witness need not be reachable; also require a port failure
        # on a genuine trace entered through the same first reset as the proof.
        witness = run / 'reset_trace-cex.json'
        code, log = command('reset_trace', 'read_rtlil model.il\n'
                            'sat -seq 5 -set-at 1 reset_n 0 -prove-skip 1 -prove ports_ok 1 '
                            f'-verify -show-ports -dump_json {witness.name}\n')
        if code == 0 or 'model found: FAIL!' not in log or not witness.is_file():
            raise ValueError(f'No reset-reachable port counterexample: {run}')
        result['reset_trace'] = {'status': 'counterexample', 'sha256': sha(witness)}
    else:
        # Exercise the induction premise for every legal occupancy using reachable states.
        for length, steps in ((0, 2), (1, 3), (2, 4)):
            name = f'reachable_{length}'
            stimulus = '-set reset_n 1 -set-at 1 reset_n 0 -set in_valid 1 -set out_ready 0'
            code, log = command(name, f'read_rtlil model.il\n'
                                 f'sat -seq {steps} {stimulus} -set-at {steps} length {length} '
                                 f'-set-at {steps} relation_ok 1 -show-ports -dump_json {name}.json\n')
            witness = run / f'{name}.json'
            if code != 0 or 'model found:' not in log or not witness.is_file():
                raise ValueError(f'No reachability witness for occupancy {length}: {run}')
            result[name] = {'status': 'reachable', 'sha256': sha(witness)}
    return result


def run_proof(output: Path, *, yosys: str | None = None, timeout: int = 60) -> dict:
    if timeout <= 0:
        raise ValueError('Timeout must be positive')
    tool = yosys or find_yosys()
    if tool is None:
        raise ValueError('Yosys is required')
    executable = shutil.which(tool) or tool
    tool = str(Path(executable).resolve())
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    version = subprocess.run([tool, '-V'], capture_output=True, text=True,
                             encoding='utf-8', errors='replace', timeout=timeout, check=True).stdout.strip()
    fingerprints = {'dut.sv': sha(DUT), 'spec.sv': sha(SPEC), 'runner': sha(Path(__file__))}
    original = DUT.read_bytes()
    model = SPEC.read_bytes()
    verification = {}
    for name in ('original', *MUTANTS):
        run = output / name
        run.mkdir()
        source, negative_stage = original, None
        if name in MUTANTS:
            before, after, negative_stage = MUTANTS[name]
            if original.count(before.encode()) != 1:
                raise ValueError(f'Expected one anchor for {name}')
            source = original.replace(before.encode(), after.encode())
        (run / 'dut.sv').write_bytes(source)
        (run / 'spec.sv').write_bytes(model)
        if sha(run / 'spec.sv') != fingerprints['spec.sv'] or (
                name == 'original' and sha(run / 'dut.sv') != fingerprints['dut.sv']):
            raise ValueError('Proof snapshot inputs changed')
        verification[name] = prove(run, tool, timeout, negative_stage)
    if (sha(DUT), sha(SPEC), sha(Path(__file__))) != (
            fingerprints['dut.sv'], fingerprints['spec.sv'], fingerprints['runner']):
        raise ValueError('Proof inputs changed during execution')
    summary = {
        'schema_version': 1, 'status': 'pass', 'tool_version': version,
        'contract': {'scope': 'two-slot FIFO protocol correctness, independent of RTL-to-netlist LEC',
                     'initial_state': 'arbitrary Boolean before one active-low synchronous reset edge',
                     'subsequent_inputs': 'unconstrained Boolean reset, request, ready and 16-bit payload at each edge',
                     'clock': 'one rising-edge clock'},
        'inputs': fingerprints, 'verification': verification,
        'artifacts': {p.relative_to(output).as_posix(): sha(p)
                      for p in sorted(output.rglob('*')) if p.is_file()},
    }
    (output / 'result.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New report and snapshot directory')
    parser.add_argument('--yosys')
    parser.add_argument('--timeout', type=int, default=60, help='Timeout per tool invocation in seconds')
    args = parser.parse_args()
    try:
        run_proof(args.output, yosys=args.yosys, timeout=args.timeout)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'FIFO specification proof failed: {exc}\n')
    print(f'FIFO specification proven; three negative controls produced SAT witnesses: {args.output / "result.json"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
