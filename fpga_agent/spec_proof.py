"""Execute a reviewed single-clock reset/induction specification contract."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from .evidence import local_file, sha
from .lec import find_yosys


@dataclass(frozen=True)
class Cover:
    signal: str
    value: int
    steps: int
    stimulus: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class Mutation:
    before: bytes
    after: bytes
    obligation: str
    steps: int
    unit: int = 0


@dataclass(frozen=True)
class SpecContract:
    name: str
    dut: Path
    reference: Path
    dut_top: str
    reference_top: str
    inputs: dict[str, int]
    reset: str
    active: int
    projections: tuple[tuple[str, str], ...]
    covers: dict[str, Cover]
    mutations: dict[str, Mutation]
    additional_dut: tuple[Path, ...] = ()
    parameters: dict[str, int] = field(default_factory=dict)


def _dut_units(contract: SpecContract) -> dict[str, Path]:
    return {'dut.sv': contract.dut, **{f'dut_{i}.sv': path
                                     for i, path in enumerate(contract.additional_dut, 1)}}


IDENT = re.compile(r'[a-zA-Z_][a-zA-Z_0-9]*')
OBSERVABLE = re.compile(r'[a-zA-Z_][a-zA-Z_0-9]*(?:\[[0-9]+\])?')
REGISTERS = {'$dff', '$dffe', '$sdff', '$sdffe', '$sdffce'}
COMBINATIONAL = {
    '$add', '$sub', '$and', '$or', '$xor', '$not', '$mux', '$pmux',
    '$logic_and', '$logic_or', '$logic_not', '$eq', '$eqx', '$ne',
    '$reduce_bool', '$reduce_and', '$reduce_or', '$lt', '$le', '$scopeinfo',
}
OBLIGATIONS = ('reset_base', 'preservation', 'observation')
MARKERS = {'proven': 'no model found: SUCCESS!', 'counterexample': 'model found: FAIL!',
           'reachable': 'SAT solving finished - model found:'}


def check_contract(contract: SpecContract) -> None:
    names = [contract.name, contract.dut_top, contract.reference_top, contract.reset,
             *contract.inputs, *contract.covers, *contract.mutations, *contract.parameters]
    if not all(IDENT.fullmatch(name) for name in names) or 'original' in contract.mutations:
        raise ValueError('Expected simple contract identifiers')
    if (contract.inputs.get('clk') != 1 or contract.inputs.get(contract.reset) != 1
            or contract.reset == 'clk' or contract.active not in (0, 1)
            or any(type(width) is not int or width <= 0 for width in contract.inputs.values())
            or contract.dut_top == contract.reference_top):
        raise ValueError('Expected an explicit scalar clock and a distinct scalar reset')
    if (len({alias for _, alias in contract.projections}) != len(contract.projections)
            or any(not OBSERVABLE.fullmatch(source) or not IDENT.fullmatch(alias)
                   for source, alias in contract.projections)):
        raise ValueError('Invalid internal observation mapping')
    if set(contract.covers) & {'prepare', *OBLIGATIONS}:
        raise ValueError('Cover names must differ from proof steps')
    if not contract.covers or not contract.mutations:
        raise ValueError('Expected reachability checks and negative controls')
    for cover in contract.covers.values():
        if not IDENT.fullmatch(cover.signal) or cover.value < 0 or cover.steps < 2:
            raise ValueError('Invalid reachability target')
        for signal, value in cover.stimulus:
            if signal not in contract.inputs or signal in ('clk', contract.reset) or not 0 <= value < 2**contract.inputs[signal]:
                raise ValueError('Reachability stimulus must constrain a non-clock, non-reset input')
    for mutation in contract.mutations.values():
        if (not mutation.before or mutation.before == mutation.after or mutation.obligation not in OBLIGATIONS
                or mutation.steps < 2 or type(mutation.unit) is not int or not 0 <= mutation.unit < len(_dut_units(contract))):
            raise ValueError('Invalid negative control')
    if any(type(value) is not int or value < 0 for value in contract.parameters.values()):
        raise ValueError('Only nonnegative integer DUT parameter defaults are supported')
    paths = [path.resolve() for path in [*_dut_units(contract).values(), contract.reference]]
    if len(paths) != len(set(paths)):
        raise ValueError('DUT compile units and reference must be distinct')


def prepare_script(contract: SpecContract) -> str:
    lines = ['read_verilog -sv ' + ' '.join(_dut_units(contract)), f'hierarchy -check -top {contract.dut_top}',
             'proc', 'memory_map', 'opt_clean', 'write_json dut_model.json']
    renames = [(source, alias) for source, alias in contract.projections if source != alias]
    if renames:
        lines += [f'cd {contract.dut_top}', *(f'rename \\{source} {alias}' for source, alias in renames), 'cd ..']
    if contract.projections:
        lines.append('expose ' + ' '.join(f'{contract.dut_top}/w:{alias}' for _, alias in contract.projections))
    lines += ['read_verilog -sv reference.sv', f'hierarchy -check -top {contract.reference_top}',
              'proc', 'write_json harness.json', 'flatten', 'opt', 'check -assert',
              'write_json model.json', 'write_rtlil model.il']
    return '\n'.join(lines) + '\n'


def validate_model(path: Path, contract: SpecContract) -> dict:
    """SAT treats each step as a rising edge; no hidden clocks, state init, or assumptions."""
    modules = json.loads(path.read_text(encoding='utf-8'))['modules']
    if set(modules) != {contract.reference_top}:
        raise ValueError('Expected one flattened proof model')
    model = modules[contract.reference_top]
    ports = model['ports']
    inputs = {name: len(port['bits']) for name, port in ports.items() if port['direction'] == 'input'}
    if inputs != contract.inputs:
        raise ValueError('Unexpected formal input interface')
    bits = [bit for name in inputs for bit in ports[name]['bits']]
    if len(set(bits)) != sum(inputs.values()) or any(type(bit) is not int for bit in bits):
        raise ValueError('Formal input bits must be distinct and unconstrained')
    for name in ('ports_ok', 'relation_ok', *(cover.signal for cover in contract.covers.values())):
        if name not in ports or ports[name]['direction'] != 'output':
            raise ValueError(f'Missing proof output: {name}')
    for name in ('ports_ok', 'relation_ok'):
        if len(ports[name]['bits']) != 1:
            raise ValueError(f'Expected scalar {name}')
    for cover in contract.covers.values():
        if cover.value >= 2**len(ports[cover.signal]['bits']):
            raise ValueError('Reachability target exceeds its port width')
    clock = ports['clk']['bits']
    state_bits = 0
    for cell in model['cells'].values():
        connections = cell['connections']
        if cell['type'] in REGISTERS:
            if (connections['CLK'] != clock or int(cell['parameters']['CLK_POLARITY'], 2) != 1
                    or any(clock[0] in pins for port, pins in connections.items() if port != 'CLK')):
                raise ValueError('Expected registers on the sole rising-edge clock')
            state_bits += len(connections['Q'])
        elif cell['type'] not in COMBINATIONAL or any(clock[0] in pins for pins in connections.values()):
            raise ValueError(f'Unsupported cell or clock use: {cell["type"]}')
    if not state_bits or any('init' in wire['attributes'] for wire in model['netnames'].values()):
        raise ValueError('Expected nonempty, unconstrained initial state')
    if any(clock[0] in port['bits'] for port in ports.values() if port['direction'] == 'output'):
        raise ValueError('Raw clock cannot be used as a proof output')
    return {'state_bits': state_bits, 'input_bits_excluding_clock': len(bits)-1}


def _validate_dut_binding(run: Path, contract: SpecContract) -> None:
    modules = json.loads((run/'dut_model.json').read_text(encoding='utf-8'))['modules']
    dut = modules[contract.dut_top]
    inputs = {name: len(port['bits']) for name, port in dut['ports'].items()
              if port['direction'] == 'input'}
    if inputs != contract.inputs or any(port['direction'] == 'inout' for port in dut['ports'].values()):
        raise ValueError('Reviewed input interface must cover every DUT input and exclude inouts')
    defaults = dut.get('parameter_default_values', {})
    for name, value in contract.parameters.items():
        if name not in defaults or int(defaults[name], 2) != value:
            raise ValueError(f'DUT parameter {name} default does not match reviewed value {value}')
    if set(defaults) != set(contract.parameters):
        raise ValueError('Reviewed parameters must cover all DUT top defaults')
    harness = json.loads((run/'harness.json').read_text(encoding='utf-8'))['modules'][contract.reference_top]
    instances = [cell for cell in harness['cells'].values() if cell['type'] == contract.dut_top]
    if len(instances) != 1 or instances[0]['parameters']:
        raise ValueError('Reference must instantiate exactly one DUT using its checked defaults')
    for name in contract.inputs:
        if instances[0]['connections'].get(name) != harness['ports'][name]['bits']:
            raise ValueError(f'Reference must directly connect DUT input {name}')


def _prove(run: Path, tool: str, timeout: int, contract: SpecContract,
           mutant: Mutation | None = None) -> dict:
    def execute(stage: str, commands: str) -> tuple[int, str]:
        script = run / f'{stage}.ys'
        script.write_text(commands, encoding='ascii')
        completed = subprocess.run([tool, '-Q', '-T', '-s', script.name], cwd=run,
                                   capture_output=True, encoding='utf-8', errors='replace',
                                   check=False, timeout=timeout)
        log = completed.stdout + completed.stderr
        (run / f'{stage}.log').write_text(log, encoding='utf-8')
        return completed.returncode, log

    if execute('prepare', prepare_script(contract))[0]:
        raise ValueError(f'Model preparation failed: {run / "prepare.log"}')
    _validate_dut_binding(run, contract)
    inventory = validate_model(run / 'model.json', contract)

    def sat(stage: str, options: str, expect: str) -> dict:
        witness = f'{stage}-witness.json'
        code, log = execute(stage, 'read_rtlil model.il\nsat -enable_undef -set-init-def -set-def-inputs '
                            f'{options} -show-ports -dump_json {witness}\n')
        if (code != 0) != (expect == 'counterexample') or MARKERS[expect] not in log:
            raise ValueError(f'{stage} expected {expect}: {run / (stage + ".log")}')
        result = {'status': expect}
        if expect != 'proven':
            waveform = json.loads((run / witness).read_text(encoding='utf-8'))
            if not waveform['signal']:
                raise ValueError(f'{stage} returned an empty witness')
            result['witness'] = witness
        return result

    first_reset = f'-set-at 1 {contract.reset} {contract.active}'
    obligations = {
        'reset_base': f'-seq 2 {first_reset} -prove-skip 1 -prove relation_ok 1 -prove ports_ok 1',
        'preservation': '-seq 2 -set-at 1 relation_ok 1 -prove-skip 1 -prove relation_ok 1',
        'observation': '-seq 1 -set-at 1 relation_ok 1 -prove ports_ok 1',
    }
    checks = {}
    for stage, query in obligations.items():
        reject = mutant is not None and stage == mutant.obligation
        checks[stage] = sat(stage, query+' -verify', 'counterexample' if reject else 'proven')
        if reject:
            break
    if mutant is not None:
        checks['reset_trace'] = sat('reset_trace', f'-seq {mutant.steps} {first_reset} '
                                    '-prove-skip 1 -prove ports_ok 1 -verify', 'counterexample')
    else:
        for name, cover in contract.covers.items():
            schedule = ' '.join(f'-set {signal} {value}' for signal, value in cover.stimulus)
            checks[name] = sat(name, f'-seq {cover.steps} -set {contract.reset} {1-contract.active} '
                               f'{first_reset} {schedule} -set-at {cover.steps} {cover.signal} {cover.value} '
                               f'-set-at {cover.steps} relation_ok 1 -set-at {cover.steps} ports_ok 1', 'reachable')
    return {'inventory': inventory, 'checks': checks}


def _fingerprints(contract: SpecContract) -> dict[str, str]:
    config = asdict(contract)
    del config['dut'], config['reference'], config['additional_dut']
    return {**{name: sha(path) for name, path in _dut_units(contract).items()},
            'reference.sv': sha(contract.reference),
            'runner.py': sha(Path(__file__)),
            'config': hashlib.sha256(json.dumps(config, default=bytes.hex).encode()).hexdigest()}


def _boundary(contract: SpecContract) -> dict:
    return {'clock': 'clk', 'edge': 'rising', 'reset': contract.reset, 'active': contract.active,
            'start': 'after first synchronous reset edge; arbitrary Boolean initial state',
            'subsequent_inputs': 'arbitrary Boolean on every subsequent edge',
            'inputs': contract.inputs, 'projections': [list(pair) for pair in contract.projections],
            'dut_top': contract.dut_top, 'reference_top': contract.reference_top,
            'parameters': contract.parameters, 'compile_units': list(_dut_units(contract))}


def _mutate(source: bytes, mutation: Mutation) -> bytes:
    if source.count(mutation.before) != 1:
        raise ValueError('Expected exactly one mutation anchor')
    return source.replace(mutation.before, mutation.after)


def _snapshot(units: dict[str, bytes], mutation: Mutation | None) -> dict[str, bytes]:
    if mutation is None:
        return units
    name = tuple(units)[mutation.unit]
    return {**units, name: _mutate(units[name], mutation)}


def validate_report(contract: SpecContract, output: Path) -> None:
    """Check current provenance and proof inventory; use run_spec to rerun SAT."""
    check_contract(contract)
    output = output.resolve()
    report = json.loads(local_file(output, 'result.json').read_text(encoding='utf-8'))
    if (report['schema_version'] != 1 or report['kind'] != 'synchronous-specification-proof'
            or report['status'] != 'pass' or report['name'] != contract.name
            or report['inputs'] != _fingerprints(contract) or report['contract'] != _boundary(contract)):
        raise ValueError('Stale or incompatible specification report')
    artifacts = {p.relative_to(output).as_posix() for p in output.rglob('*')
                 if p.is_file() and p != output/'result.json'}
    if set(report['artifacts']) != artifacts or any(
            sha(local_file(output, name)) != digest for name, digest in report['artifacts'].items()):
        raise ValueError('Specification proof evidence changed')
    runs = [('original', None), *contract.mutations.items()]
    if set(report['verification']) != {name for name, _ in runs}:
        raise ValueError('Missing or extra specification run')
    original = {name: path.read_bytes() for name, path in _dut_units(contract).items()}
    for name, mutation in runs:
        run = output / name
        item = report['verification'][name]
        expected_dut = _snapshot(original, mutation)
        _validate_dut_binding(run, contract)
        if (any((run/file).read_bytes() != source for file, source in expected_dut.items())
                or sha(run/'reference.sv') != report['inputs']['reference.sv']
                or item['inventory'] != validate_model(run/'model.json', contract)):
            raise ValueError(f'Snapshot or model inventory mismatch: {name}')
        expected = {}
        for stage in OBLIGATIONS:
            reject = mutation is not None and stage == mutation.obligation
            expected[stage] = 'counterexample' if reject else 'proven'
            if reject:
                break
        if mutation is None:
            expected.update({cover: 'reachable' for cover in contract.covers})
        else:
            expected['reset_trace'] = 'counterexample'
        if set(item['checks']) != set(expected):
            raise ValueError(f'Missing or extra specification obligation: {name}')
        required = {*expected_dut, 'reference.sv', 'prepare.ys', 'prepare.log', 'dut_model.json',
                    'harness.json', 'model.il', 'model.json'}
        for stage, status in expected.items():
            required.update({f'{stage}.log', f'{stage}.ys'})
            check = item['checks'][stage]
            if check['status'] != status or MARKERS[status] not in (run/f'{stage}.log').read_text(encoding='utf-8'):
                raise ValueError(f'Unexpected specification result: {name}/{stage}')
            if status != 'proven':
                required.add(f'{stage}-witness.json')
                if check['witness'] != f'{stage}-witness.json' or not json.loads(
                        (run/check['witness']).read_text(encoding='utf-8'))['signal']:
                    raise ValueError(f'Missing specification witness: {name}/{stage}')
        if not {f'{name}/{file}' for file in required} <= artifacts:
            raise ValueError(f'Incomplete proof artifacts: {name}')


def run_spec(contract: SpecContract, output: Path, *, yosys: str | None = None, timeout: int = 60) -> dict:
    check_contract(contract)
    if timeout <= 0:
        raise ValueError('Timeout must be positive')
    tool = yosys or find_yosys()
    if tool is None:
        raise ValueError('Yosys is required')
    tool = str(Path(shutil.which(tool) or tool).resolve())
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    version = subprocess.run([tool, '-V'], capture_output=True, text=True, encoding='utf-8',
                             errors='replace', timeout=timeout, check=True).stdout.strip()
    digests = _fingerprints(contract)
    original = {name: path.read_bytes() for name, path in _dut_units(contract).items()}
    reference = contract.reference.read_bytes()
    verification = {}
    for name, mutation in [('original', None), *contract.mutations.items()]:
        run = output / name
        run.mkdir()
        for file, source in _snapshot(original, mutation).items():
            (run / file).write_bytes(source)
        (run / 'reference.sv').write_bytes(reference)
        verification[name] = _prove(run, tool, timeout, contract, mutation)

    if _fingerprints(contract) != digests:
        raise ValueError('Proof inputs changed during execution')
    if any(sha(output / name / 'reference.sv') != digests['reference.sv'] for name in verification):
        raise ValueError('Reference snapshots do not match')
    if any(sha(output/'original'/name) != digests[name] for name in original):
        raise ValueError('DUT snapshot does not match')
    summary = {
        'schema_version': 1, 'kind': 'synchronous-specification-proof', 'status': 'pass',
        'name': contract.name, 'tool_version': version, 'inputs': digests,
        'contract': _boundary(contract),
        'verification': verification,
        'artifacts': {p.relative_to(output).as_posix(): sha(p) for p in sorted(output.rglob('*')) if p.is_file()},
    }
    (output / 'result.json').write_text(json.dumps(summary, indent=2)+'\n', encoding='utf-8')
    return summary
