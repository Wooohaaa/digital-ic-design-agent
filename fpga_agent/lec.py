"""Conservative Yosys RTL-to-generic-netlist equivalence with bound evidence.

No weak temporal induction or ignored unknown cells. State proof has an explicit
single-clock, corresponding-Boolean-state contract.
This is a scoped external-tool adapter, not a SystemVerilog implementation.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .evidence import hdl_input_hashes, local_file, sha
from .project import load_manifest


SCHEMA = 3
STATE_METHODS = {'state-bijection', 'reset-state-relation'}


def proof_method(manifest: dict) -> str:
    config = manifest.get('formal_equivalence', {})
    if not isinstance(config, dict) or config.get('method', 'direct') not in {'direct', *STATE_METHODS}:
        raise ValueError('formal_equivalence.method must be direct, state-bijection or reset-state-relation')
    if config.get('method') == 'reset-state-relation':
        reset = config.get('reset_start')
        if (not isinstance(reset, dict) or set(reset) != {'signal', 'active'}
                or not isinstance(reset['signal'], str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', reset['signal'])
                or type(reset['active']) is not int or reset['active'] not in {0, 1}):
            raise ValueError('reset-state-relation requires explicit reset_start {signal, active: 0 or 1}')
    elif 'reset_start' in config:
        raise ValueError('reset_start is only allowed with reset-state-relation')
    return config.get('method', 'direct')


def proof_contract(manifest: dict) -> dict:
    method = proof_method(manifest)
    if method == 'reset-state-relation':
        return {'start': 'after-declared-asynchronous-reset',
                'reset_start': dict(manifest['formal_equivalence']['reset_start']),
                'pre_reset_behavior': 'excluded'}
    return {'start': 'matching-initial-state-sets' if method == 'state-bijection' else 'direct-equivalence'}


def proof_top(manifest: dict) -> str:
    proof_method(manifest)
    top = manifest.get('formal_equivalence', {}).get('target_top', manifest['top_module'])
    if not isinstance(top, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', top):
        raise ValueError('LEC target_top must be a simple module identifier')
    return top


def find_yosys() -> str | None:
    for name in ('yosys', 'yowasp-yosys'):
        found = shutil.which(name)
        if found:
            return found
    bundled = Path(sys.executable).parent / ('yowasp-yosys.exe' if sys.platform == 'win32' else 'yowasp-yosys')
    return str(bundled) if bundled.is_file() else None


def lec_inputs(project: Path, netlist: str | None = None) -> dict[str, str]:
    manifest = load_manifest(project)
    proof_method(manifest)
    top, sources = proof_top(manifest), manifest.get('rtl_sources')
    if not isinstance(sources, list) or not sources or any(not isinstance(s, str) for s in sources):
        raise ValueError('LEC requires nonempty rtl_sources')
    inputs = hdl_input_hashes(project, sources + ([netlist] if netlist else []))
    # Deliberately restrict the first adapter to portable literal file names.
    if any(not re.fullmatch(r'[A-Za-z0-9_./ -]+', name) or name.startswith('-') for name in inputs):
        raise ValueError('LEC input path contains unsupported characters')
    import hashlib
    inputs['@config'] = hashlib.sha256(json.dumps({
        'top': top, 'sources': sources, 'netlist': netlist,
        'formal_equivalence': manifest.get('formal_equivalence'),
    }, sort_keys=True).encode()).hexdigest()
    inputs['@runner'] = sha(Path(__file__))
    inputs['@state_checker'] = sha(Path(__file__).with_name('lec_state.py'))
    return inputs


def _proof_count(path: Path) -> int:
    module = json.loads(path.read_text(encoding='utf-8'))['modules']['equiv']
    if not any(port['direction'] == 'output' and port['bits'] for port in module['ports'].values()):
        raise ValueError('No observable output ports')
    cells = [c for c in module['cells'].values() if c['type'] == '$equiv']
    if not cells or any(c['connections']['A'] != c['connections']['B'] for c in cells):
        raise ValueError('Empty or unproven equivalence inventory')
    return len(cells)


def run_lec(project: Path, *, yosys: str | None = None, timeout: int = 120,
            netlist: str | None = None) -> dict:
    if timeout <= 0:
        raise ValueError('timeout must be positive')
    project = project.resolve()
    inputs = lec_inputs(project, netlist)
    manifest = load_manifest(project)
    top = proof_top(manifest)
    method = proof_method(manifest)
    reset_start = manifest.get('formal_equivalence', {}).get('reset_start')
    run = project / 'build' / 'lec' / uuid.uuid4().hex
    run.mkdir(parents=True)
    # Immutable per-run copies prevent the proof silently reading changed RTL.
    for name, digest in inputs.items():
        if name.startswith('@'):
            continue
        target = run / 'inputs' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(local_file(project, name).read_bytes())
        if sha(target) != digest:
            raise ValueError(f'LEC snapshot changed: {name}')
    sources = ' '.join('"' + local_file(project, name).relative_to(project).as_posix() + '"'
                       for name in manifest['rtl_sources'])
    state_normalize = 'dffunmap\nopt_clean\n' if method in STATE_METHODS else ''
    memory_pass = 'memory -nordff' if method in STATE_METHODS else 'memory'
    normalize = (f'read_verilog -sv -I. {sources}\n'
                 f'hierarchy -check -top {top}\nproc\nflatten\n{memory_pass}\nopt\ncheck -assert\n'
                 + state_normalize + f'rename {top} gold\nwrite_rtlil ../gold.il\n'
                 + ('write_json ../gold.json\n' if method in STATE_METHODS else ''))
    synth = (f'read_rtlil gold.il\nrename gold {top}\n'
             f'synth -top {top} -flatten -noabc' + (' -nofsm' if method in STATE_METHODS else '')
             + '\ncheck -assert\nwrite_verilog -noattr netlist.v\n')
    candidate = 'netlist.v'
    if netlist:
        candidate = 'inputs/' + local_file(project, netlist).relative_to(project).as_posix()
    prove = (f'read_rtlil gold.il\nread_verilog "{candidate}"\n'
             f'proc\nmemory\nopt\nrename {top} gate\n'
             'equiv_make gold gate equiv\nhierarchy -check -top equiv\n'
             'equiv_simple -undef\nequiv_status -assert\nwrite_json proof.json\n')
    if method in STATE_METHODS:
        gate_candidate = candidate.removeprefix('inputs/') if netlist else '../netlist.v'
        prepare = (f'read_verilog -I. "{gate_candidate}"\nhierarchy -check -top {top}\n'
                   'proc\nflatten\nmemory -nordff\nopt\ndffunmap\nopt_clean\ncheck -assert\nwrite_json ../gate.json\n')
        (run / 'gate_prepare.ys').write_text(prepare, encoding='utf-8')
        prove = ('read_json state_miter.json\nhierarchy -check -top state_miter\ncheck -assert\n'
                 'techmap\nopt -full\ncheck -assert\n'
                 'sat -enable_undef -set-def-inputs -prove proof_ok 1 -verify '
                 '-show-inputs -show-outputs -dump_json counterexample.json\n')
    (run / 'inputs' / 'normalize.ys').write_text(normalize, encoding='utf-8')
    (run / 'synth.ys').write_text(synth, encoding='utf-8')
    (run / 'prove.ys').write_text(prove, encoding='utf-8')
    report = {'schema_version': SCHEMA, 'status': 'blocked', 'started_at': datetime.now(timezone.utc).isoformat(),
              'inputs': inputs, 'candidate_netlist': netlist, 'run_directory': run.relative_to(project).as_posix(),
              'tool_version': '', 'method': method, 'stages': [], 'artifacts': {}, 'proven_cells': 0,
              'contract': proof_contract(manifest),
              'proof_top': top, 'design_top': manifest['top_module'],
              'scope': 'generic-cell synthesis; ' + method + '; no physical signoff',
              'reason': '', 'elapsed_seconds': 0.0}
    tool = yosys or find_yosys()
    logs = []
    start = time.monotonic()
    deadline = start + timeout

    def execute(cwd: Path, args: list[str], stage: str) -> subprocess.CompletedProcess:
        command = [tool, *args]
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=max(0.001, deadline-time.monotonic()), check=False)
        output = result.stdout + result.stderr
        logs.append(f'[{stage}]\n{output}')
        report['stages'].append({'stage': stage, 'command': command, 'returncode': result.returncode})
        if result.returncode:
            raise RuntimeError(f'{stage} failed (exit {result.returncode}); see lec.log')
        return result

    try:
        if not tool:
            raise FileNotFoundError('Yosys unavailable; install yosys or yowasp-yosys')
        report['tool_version'] = execute(run, ['-V'], 'version').stdout.strip()
        execute(run / 'inputs', ['-Q', '-T', '-s', 'normalize.ys'], 'normalize')
        if not netlist:
            execute(run, ['-Q', '-T', '-s', 'synth.ys'], 'synthesis')
        if method in STATE_METHODS:
            from .lec_state import build_state_miter, state_proof_valid
            execute(run / 'inputs', ['-Q', '-T', '-s', '../gate_prepare.ys'], 'gate_prepare')
            miter, relation = build_state_miter(run / 'gold.json', run / 'gate.json', top, reset_start=reset_start)
            for name, data in (('state_miter.json', miter), ('state_relation.json', relation)):
                (run / name).write_text(json.dumps(data, indent=2)+'\n', encoding='utf-8')
            execute(run, ['-Q', '-T', '-s', 'prove.ys'], 'state_proof')
            (run / 'lec.log').write_text('\n'.join(logs), encoding='utf-8')
            report['proven_cells'], report['state_relation'] = state_proof_valid(run, top, reset_start=reset_start)
        else:
            execute(run, ['-Q', '-T', '-s', 'prove.ys'], 'proof')
            report['proven_cells'] = _proof_count(run / 'proof.json')
        if lec_inputs(project, netlist) != inputs:
            raise ValueError('LEC inputs changed during execution')
        report['status'], report['reason'] = 'pass', 'All ' + method + ' equivalence obligations proven'
    except FileNotFoundError as exc:
        report['reason'] = str(exc)
    except subprocess.TimeoutExpired:
        report['status'], report['reason'] = 'fail', 'LEC timed out; no proof accepted'
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        report['status'], report['reason'] = 'fail', str(exc)
    report['elapsed_seconds'] = round(time.monotonic()-start, 3)
    (run / 'lec.log').write_text('\n'.join(logs), encoding='utf-8')
    report['artifacts'] = {p.relative_to(project).as_posix(): sha(p) for p in run.rglob('*') if p.is_file()}
    destination = project / 'reports'
    destination.mkdir(exist_ok=True)
    pending = destination / f'.lec-{uuid.uuid4().hex}.json'
    pending.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    pending.replace(destination / 'lec.json')
    return report


def lec_valid(project: Path, report: dict) -> tuple[bool, str]:
    """Reject stale, partial, edited, or empty proof evidence."""
    try:
        if report.get('status') != 'pass' or report.get('schema_version') != SCHEMA:
            return False, 'No current passing LEC report'
        if report.get('inputs') != lec_inputs(project, report.get('candidate_netlist')):
            return False, 'RTL, LEC configuration, or checker changed'
        method = proof_method(load_manifest(project))
        if report.get('method') != method:
            return False, 'LEC proof method changed'
        if report.get('contract') != proof_contract(load_manifest(project)):
            return False, 'LEC proof start contract changed or missing'
        manifest = load_manifest(project)
        if report.get('proof_top') != proof_top(manifest) or report.get('design_top') != manifest['top_module']:
            return False, 'LEC target scope changed or missing'
        run = local_file(project, report['run_directory'] + '/gold.il').parent
        stages = report['stages']
        expected = ['version', 'normalize', *([] if report.get('candidate_netlist') else ['synthesis']),
                    *(['gate_prepare', 'state_proof'] if method in STATE_METHODS else ['proof'])]
        if [s['stage'] for s in stages] != expected or any(type(s['returncode']) is not int or s['returncode'] != 0 for s in stages):
            return False, 'LEC stages incomplete or failed'
        required = {run / 'gold.il', run / 'prove.ys', run / 'inputs/normalize.ys', run / 'lec.log'}
        required |= {run / name for name in (('gold.json', 'gate.json', 'state_miter.json',
                    'state_relation.json', 'gate_prepare.ys') if method in STATE_METHODS else ('proof.json',))}
        if not report.get('candidate_netlist'):
            required |= {run / 'netlist.v', run / 'synth.ys'}
        required |= {run / 'inputs' / name for name in report['inputs'] if not name.startswith('@')}
        artifacts = report['artifacts']
        if not required.issubset({(project / name).resolve() for name in artifacts}):
            return False, 'LEC artifacts incomplete'
        if any(sha(local_file(project, name)) != digest for name, digest in artifacts.items()):
            return False, 'LEC artifact changed'
        if method in STATE_METHODS:
            from .lec_state import state_proof_valid
            manifest = load_manifest(project)
            count, relation = state_proof_valid(run, proof_top(manifest),
                                               reset_start=manifest.get('formal_equivalence', {}).get('reset_start'))
            if relation != report.get('state_relation'):
                return False, 'LEC state relation changed'
        else:
            count = _proof_count(run / 'proof.json')
        if count != report['proven_cells']:
            return False, 'LEC proof inventory changed'
        return True, 'Current ' + method + (' (after declared reset)' if method == 'reset-state-relation' else '') + ' equivalence verified for ' + proof_top(manifest)
    except (OSError, ValueError, KeyError, TypeError):
        return False, 'LEC evidence missing or malformed'
