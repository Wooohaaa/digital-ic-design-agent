"""Check combinational RTL and generic netlists using a Yosys SAT miter."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .evidence import hdl_input_hashes, local_file, sha
from .project import load_manifest


SCHEMA = 1
SUCCESS = 'SAT proof finished - no model found: SUCCESS!'
GATES = {'$_' + name + '_' for name in (
    'BUF', 'NOT', 'AND', 'NAND', 'OR', 'NOR', 'XOR', 'XNOR', 'MUX',
    'NMUX', 'ANDNOT', 'ORNOT', 'AOI3', 'AOI4', 'OAI3', 'OAI4',
)}


def find_yosys() -> str | None:
    for name in ('yosys', 'yowasp-yosys'):
        if tool := shutil.which(name):
            return tool
    bundled = Path(sys.executable).parent / ('yowasp-yosys.exe' if sys.platform == 'win32' else 'yowasp-yosys')
    return str(bundled) if bundled.is_file() else None


def lec_inputs(project: Path, netlist: str | None = None) -> dict[str, str]:
    manifest = load_manifest(project)
    top, sources = manifest['top_module'], manifest.get('rtl_sources')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', top):
        raise ValueError('LEC requires a simple top_module identifier')
    if not isinstance(sources, list) or not sources or any(not isinstance(name, str) for name in sources):
        raise ValueError('LEC requires nonempty rtl_sources')
    inputs = hdl_input_hashes(project, sources + ([netlist] if netlist else []))
    if any(not re.fullmatch(r'[A-Za-z0-9_./ -]+', name) or name.startswith('-') for name in inputs):
        raise ValueError('LEC input path contains unsupported characters')
    inputs['@config'] = hashlib.sha256(json.dumps([top, sources, netlist]).encode()).hexdigest()
    inputs['@runner'] = sha(Path(__file__))
    return inputs


def _design(run: Path, name: str) -> dict:
    module = json.loads((run / (name + '.json')).read_text(encoding='utf-8'))['modules'][name]
    if any(cell['type'] not in GATES for cell in module['cells'].values()):
        raise ValueError('LEC only supports combinational generic gates')
    ports = module['ports']
    if not any(p['direction'] == 'output' and p['bits'] for p in ports.values()):
        raise ValueError('No observable output ports')
    if any(p['direction'] not in {'input', 'output'} for p in ports.values()):
        raise ValueError('LEC only supports input and output ports')
    bits = [bit for port in ports.values() for bit in port['bits']]
    bits.extend(bit for cell in module['cells'].values() for conn in cell['connections'].values() for bit in conn)
    if any(bit in ('x', 'z') for bit in bits):
        raise ValueError('LEC does not support X or Z signals')
    return {port: (p['direction'], len(p['bits']), p.get('offset', 0), p.get('upto', 0))
            for port, p in ports.items()}


def _output_bits(run: Path) -> int:
    ports = _design(run, 'gold')
    if ports != _design(run, 'gate'):
        raise ValueError('RTL and netlist port interfaces differ')
    return sum(width for direction, width, _, _ in ports.values() if direction == 'output')


def run_lec(project: Path, *, yosys: str | None = None, timeout: int = 120,
            netlist: str | None = None) -> dict:
    if timeout <= 0:
        raise ValueError('timeout must be positive')
    project = project.resolve()
    inputs = lec_inputs(project, netlist)
    manifest = load_manifest(project)
    top = manifest['top_module']
    run = project / 'build' / 'lec' / uuid.uuid4().hex
    snapshot = run / 'inputs'
    snapshot.mkdir(parents=True)
    for name, digest in inputs.items():
        if not name.startswith('@'):
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(local_file(project, name).read_bytes())
            if sha(target) != digest:
                raise ValueError(f'LEC input changed during snapshot: {name}')

    def quoted(name: str) -> str:
        return '"' + local_file(project, name).relative_to(project).as_posix() + '"'

    sources = ' '.join(quoted(name) for name in manifest['rtl_sources'])
    normalize = f'hierarchy -check -top {top}\nproc\nflatten\nmemory\nopt\ncheck -assert\n'
    mapped = 'techmap\nopt\ncheck -assert\n'
    gate_prep = normalize + mapped
    scripts = {
        'gold': f'read_verilog -sv -I. {sources}\n{normalize}'
                f'write_rtlil ../rtl.il\n{mapped}rename {top} gold\nwrite_json ../gold.json\n',
        'gate': (f'read_verilog -sv -I. {quoted(netlist)}\n' if netlist else 'read_verilog ../netlist.v\n')
                + gate_prep + f'rename {top} gate\nwrite_json ../gate.json\n',
        'proof': 'read_json ../gold.json\nread_json ../gate.json\n'
                 'miter -equiv -flatten -make_outputs gold gate equiv\n'
                 'hierarchy -check -top equiv\ncheck -assert\n'
                 'sat -enable_undef -set-def-inputs -prove trigger 0 -verify '
                 '-show-inputs -show-outputs -dump_json ../counterexample.json\n',
    }
    if not netlist:
        scripts['synthesis'] = (f'read_rtlil ../rtl.il\nsynth -top {top} -flatten -noabc\n'
                                'check -assert\nwrite_verilog -noattr ../netlist.v\n')
    for name, script in scripts.items():
        (run / (name + '.ys')).write_text(script, encoding='utf-8')

    report = {'schema_version': SCHEMA, 'status': 'blocked', 'top_module': top,
              'inputs': inputs, 'candidate_netlist': netlist, 'tool_version': '',
              'run_directory': run.relative_to(project).as_posix(), 'reason': '',
              'output_bits': 0, 'stages': [], 'artifacts': {}}
    tool = yosys or find_yosys()
    deadline = time.monotonic() + timeout

    def execute(stage: str, args: list[str]) -> str:
        result = subprocess.run([tool, *args], cwd=snapshot, capture_output=True, encoding='utf-8',
                                errors='replace', timeout=max(0.001, deadline - time.monotonic()), check=False)
        transcript = result.stdout + result.stderr
        (run / (stage + '.log')).write_text(transcript, encoding='utf-8')
        report['stages'].append({'stage': stage, 'returncode': result.returncode})
        if result.returncode:
            raise RuntimeError(f'{stage} failed (exit {result.returncode}); see {stage}.log')
        return transcript

    try:
        if not tool:
            raise FileNotFoundError('Yosys unavailable; install yosys or yowasp-yosys')
        report['tool_version'] = execute('version', ['-V']).strip()
        execute('gold', ['-Q', '-T', '-s', '../gold.ys'])
        _design(run, 'gold')
        if not netlist:
            execute('synthesis', ['-Q', '-T', '-s', '../synthesis.ys'])
        execute('gate', ['-Q', '-T', '-s', '../gate.ys'])
        report['output_bits'] = _output_bits(run)
        if SUCCESS not in execute('proof', ['-Q', '-T', '-s', '../proof.ys']):
            raise ValueError('Yosys SAT success evidence missing')
        if lec_inputs(project, netlist) != inputs:
            raise ValueError('LEC inputs changed during execution')
        report['status'], report['reason'] = 'pass', 'All observable outputs proven equivalent'
    except FileNotFoundError as exc:
        report['reason'] = str(exc)
    except subprocess.TimeoutExpired:
        report['status'], report['reason'] = 'fail', 'LEC timed out; no proof accepted'
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        report['status'], report['reason'] = 'fail', str(exc)
    report['artifacts'] = {p.relative_to(project).as_posix(): sha(p) for p in run.rglob('*') if p.is_file()}
    destination = project / 'reports'
    destination.mkdir(exist_ok=True)
    pending = destination / f'.lec-{run.name}.json'
    pending.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    pending.replace(destination / 'lec.json')
    return report


def lec_valid(project: Path, report: dict) -> tuple[bool, str]:
    try:
        project = project.resolve()
        if report.get('status') != 'pass' or report.get('schema_version') != SCHEMA:
            return False, 'No current passing LEC report'
        netlist = report['candidate_netlist']
        inputs = lec_inputs(project, netlist)
        if report['inputs'] != inputs or report['top_module'] != load_manifest(project)['top_module']:
            return False, 'RTL, netlist, top module, or checker changed'
        stages = ['version', 'gold', *([] if netlist else ['synthesis']), 'gate', 'proof']
        if report['stages'] != [{'stage': stage, 'returncode': 0} for stage in stages]:
            return False, 'LEC stages incomplete or failed'
        run = local_file(project, report['run_directory'] + '/proof.log').parent
        artifacts = report['artifacts']
        required = {run / (stage + '.log') for stage in stages}
        required |= {run / (stage + '.ys') for stage in stages if stage != 'version'}
        required |= {run / name for name in ('gold.json', 'gate.json', 'rtl.il')}
        if not netlist:
            required.add(run / 'netlist.v')
        required |= {run / 'inputs' / name for name in inputs if not name.startswith('@')}
        if not {path.relative_to(project).as_posix() for path in required} <= artifacts.keys():
            return False, 'LEC evidence incomplete'
        if any(sha(local_file(project, name)) != digest for name, digest in artifacts.items()):
            return False, 'LEC artifact changed'
        if any(sha(run / 'inputs' / name) != digest for name, digest in inputs.items() if not name.startswith('@')):
            return False, 'LEC input snapshot changed'
        if (_output_bits(run) != report['output_bits']
                or (run / 'version.log').read_text(encoding='utf-8').strip() != report['tool_version']
                or SUCCESS not in (run / 'proof.log').read_text(encoding='utf-8')):
            return False, 'LEC proof or tool evidence changed'
        return True, 'Current combinational equivalence verified'
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False, 'LEC evidence missing or malformed'
