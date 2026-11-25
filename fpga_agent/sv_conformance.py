"""Executable bounded SV/UVM capability matrix with independent trace oracles."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .evidence import local_file, sha
from .sv_reference import TRACE_ORACLES, expected, vectors


SCHEMA = 4
BACKENDS = ('icarus', 'modelsim', 'verilator')


def verification_model(backend: str) -> str:
    return (f'{backend} runtime vs independent Python oracle; separate slang elaboration; '
            'capabilities apply only to the selected backend and bounded fixtures')


def default_suite() -> Path:
    return Path(__file__).parent / 'templates/conformance'


def case_sources(suite: Path, case: dict) -> list[Path]:
    extra = case.get('additional_sources', [])
    if not isinstance(extra, list) or any(not isinstance(name, str) for name in extra):
        raise ValueError('additional_sources must list local HDL paths')
    paths = [local_file(suite, name) for name in [*extra, case['source']]]
    if len(set(paths)) != len(paths):
        raise ValueError('Duplicate case source')
    return paths


def load_contract(suite: Path) -> dict:
    data = json.loads((suite / 'contract.json').read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not data.get('cases') or not data.get('features'):
        raise ValueError('Invalid conformance contract')
    cases, features = data['cases'], data['features']
    for items in (cases, features):
        ids = [item['id'] for item in items]
        if len(set(ids)) != len(ids) or any(not re.fullmatch(r'(SV|UVM)-[A-Z0-9-]+', i) for i in ids):
            raise ValueError('Invalid or duplicate capability IDs')
    known = {f['id'] for f in features}
    for case in cases:
        if case['kind'] not in {'semantic', 'reject', 'probe', 'uvm-probe'} or type(case['required']) is not bool:
            raise ValueError('Invalid conformance case kind/required flag')
        case_sources(suite, case)
        if not case['features'] or not set(case['features']) <= known:
            raise ValueError('Case references unknown features')
        if case['kind'] != 'reject' and case['id'] not in TRACE_ORACLES:
            expected(case['id'])  # All semantic expectations live in a separate checker.
    return data


def suite_inputs(suite: Path) -> dict:
    files = sorted(p for p in suite.rglob('*') if p.is_file())
    result = {p.relative_to(suite).as_posix(): sha(local_file(suite, str(p.resolve()))) for p in files}
    for name in ('sv_conformance.py', 'sv_reference.py', 'sv_slang.py'):
        result['@'+name] = sha(Path(__file__).with_name(name))
    return result


def verify_uvm(root: Path, lock: dict) -> dict[str, str]:
    names = {p.relative_to(root).as_posix() for p in (root / 'src').rglob('*')
             if p.is_file() and p.suffix in {'.sv', '.svh'}}
    names |= {'LICENSE.txt', 'NOTICE.txt', 'README.md', 'DEVIATIONS.md'}
    if names != set(lock['files']):
        raise ValueError('Official UVM source inventory differs from the lock')
    actual = {n: hashlib.sha256(local_file(root, n).read_bytes().replace(b'\r\n', b'\n')).hexdigest() for n in sorted(names)}
    if actual != lock['files']:
        raise ValueError('Official UVM sources/license differ from the pinned release')
    return actual


def trace_matches(case_id: str, output: str) -> tuple[bool, str]:
    observations, done = [], []
    fatal_case = case_id == 'UVM-FATAL'
    fatal_messages, error_counts, fatal_counts = 0, [], []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith('# '):  # ModelSim transcript prefix; keep raw logs unchanged.
            line = line[2:].lstrip()
        if re.match(r'^\*\*\s+(Error|Fatal)\b', line, re.I):
            return False, 'Simulator error/fatal evidence'
        if line.startswith('OBS'):
            fields = line.split()
            if len(fields) < 3 or fields[:2] != ['OBS', case_id]:
                return False, 'Malformed or foreign observation'
            observations.append(fields[2:])
        elif line.startswith('DONE'):
            done.append(line)
        if re.match(r'^(FAIL|FATAL|ERROR)(?:\b|:)', line, re.I):
            return False, 'Runtime error marker'
        if re.match(r'^UVM_(ERROR|FATAL)\b', line):
            summary = re.fullmatch(r'UVM_(ERROR|FATAL)\s*:\s*(\d+)', line)
            if summary:
                (error_counts if summary[1]=='ERROR' else fatal_counts).append(int(summary[2]))
            elif fatal_case and re.fullmatch(r'UVM_FATAL\b.*\[RSI_EXPECTED_FATAL\] expected fatal termination', line):
                fatal_messages += 1
            else:
                return False, 'UVM error/fatal evidence'
    if any(error_counts) or not fatal_case and any(fatal_counts):
        return False, 'UVM error/fatal evidence'
    if case_id.startswith('UVM-'):
        if error_counts != [0] or fatal_counts != [1 if fatal_case else 0]:
            return False, 'Missing, duplicate or unexpected UVM severity summary'
        if fatal_case and fatal_messages != 1:
            return False, 'Expected fatal ID/message/termination evidence missing or duplicated'
    if done != ([] if fatal_case else ['DONE '+case_id]):
        return False, 'Observation mismatch: missing or unexpected completion marker'
    if case_id in TRACE_ORACLES:
        return TRACE_ORACLES[case_id](observations)
    oracle = expected(case_id)
    if observations != oracle:
        return False, f'Observation mismatch: expected {len(oracle)} ordered rows, got {len(observations)}'
    return True, f'{len(oracle)} ordered rows match independent Python oracle'


def classify(case: dict, observation: dict) -> tuple[str, str]:
    if observation.get('blocked'):
        return 'blocked', observation['blocked']
    if observation.get('error'):
        return 'error', observation['error']
    frontend = observation['slang']
    if frontend['status'] == 'blocked':
        return 'blocked', frontend['reason']
    if frontend['status'] not in {'accepted', 'rejected'}:
        return 'error', 'Independent frontend did not complete'
    compile_result = observation['compile']
    if compile_result.get('execution_error'):
        return 'error', compile_result['execution_error']
    code = compile_result['returncode']
    if case['kind'] == 'reject':
        if (frontend['status'] == 'rejected' and frontend.get('error_count', 0) > 0
                and type(code) is int and 0 < code < 124
                and re.search(case['diagnostic'], compile_result['output'], re.I)):
            return 'pass', 'Both independent frontends reject the invalid syntax fixture'
        return 'fail', 'Invalid syntax was not rejected with the expected diagnostic by both frontends'
    if frontend['status'] != 'accepted':
        return 'gap', 'slang rejected the declared valid fixture; no runtime support claimed'
    if code != 0:
        if type(code) is int and 0 < code < 124:
            return 'gap', 'slang accepts the fixture; selected runtime frontend rejects it'
        return 'error', 'Compiler crashed or returned an abnormal status'
    build = observation.get('build')
    if build is not None and (build.get('execution_error') or build['returncode'] != 0):
        return 'error', 'Generated C++ simulation build failed or timed out; not a frontend capability gap'
    runtime = observation['runtime']
    if runtime.get('execution_error') or runtime['returncode'] != 0:
        return 'fail', 'Simulation failed or timed out'
    matched, reason = trace_matches(case['id'], runtime['output'])
    return ('pass' if matched else 'fail'), reason


def _aggregate(contract: dict, rows: list[dict]) -> tuple[str, str, bool]:
    by_id = {row['id']: row for row in rows}
    required = [case['id'] for case in contract['cases'] if case['required']]
    selected_required = [i for i in required if i in by_id]
    if any(r['status'] in {'fail', 'error'} for r in rows):
        status = 'fail'
    elif any(by_id[i]['status'] == 'gap' for i in selected_required):
        status = 'fail'
    elif any(by_id[i]['status'] == 'blocked' for i in selected_required):
        status = 'blocked'
    elif any(r['status'] in {'gap', 'blocked'} for r in rows):
        status = 'partial'
    else:
        status = 'pass'
    complete = all(i in by_id for i in required)
    baseline = 'pass' if complete and all(by_id[i]['status'] == 'pass' for i in required) else 'not_complete'
    return status, baseline, complete


def _features(contract: dict, rows: list[dict]) -> list[dict]:
    result = []
    for feature in contract['features']:
        ids = {c['id'] for c in contract['cases'] if feature['id'] in c['features']}
        matching = [r for r in rows if r['id'] in ids]
        state = ('planned' if not matching else 'fixture_verified' if len(matching)==len(ids) and all(r['status']=='pass' for r in matching)
                 else 'partially_verified' if all(r['status']=='pass' for r in matching)
                 else 'frontend_only' if all(r['status']=='gap' and r['frontend_status']=='accepted' for r in matching)
                 else 'blocked' if all(r['status']=='blocked' for r in matching) else 'not_verified')
        result.append({**feature, 'status': state, 'cases': [r['id'] for r in matching]})
    return result


def _uvm_profile_status(contract: dict, rows: list[dict]) -> str:
    ids = {c['id'] for c in contract['cases'] if c['kind']=='uvm-probe'}
    passed = {r['id'] for r in rows if r['status']=='pass'}
    return 'pass' if ids and ids <= passed else 'not_complete'


def run_conformance(output: Path, *, suite: Path | None = None, case_ids: list[str] | None = None,
                    required_only: bool = False, uvm_root: Path | None = None, timeout: int = 60,
                    backend: str = 'icarus') -> dict:
    suite = (suite or default_suite()).resolve()
    if timeout <= 0 or required_only and case_ids:
        raise ValueError('Positive timeout required; do not combine case IDs with required_only')
    if backend not in BACKENDS:
        raise ValueError('Unknown runtime backend')
    contract = load_contract(suite)
    selected = [c for c in contract['cases'] if (not case_ids or c['id'] in case_ids) and (not required_only or c['required'])]
    if not selected or case_ids and (len(set(case_ids)) != len(case_ids) or set(case_ids) != {c['id'] for c in selected}):
        raise ValueError('Unknown, empty or duplicate case selection')
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    run = output.parent / 'sv-runs' / uuid.uuid4().hex
    run.mkdir(parents=True)
    inputs = suite_inputs(suite)
    shutil.copytree(suite, run / 'suite')
    for name, digest in inputs.items():
        if not name.startswith('@') and sha(run/'suite'/name) != digest:
            raise ValueError('Conformance snapshot does not match original inputs')
    if suite_inputs(suite) != inputs:
        raise ValueError('Conformance inputs changed during snapshot')
    if backend == 'icarus':
        compiler, runtime = shutil.which('iverilog'), shutil.which('vvp')
    elif backend == 'modelsim':
        compiler, runtime = shutil.which('vlog'), shutil.which('vsim')
    else:
        compiler = shutil.which('verilator') or shutil.which('verilator_bin')
        runtime = compiler  # The actual executable is generated per case, not searched on PATH.
    make = (shutil.which('make') or shutil.which('mingw32-make')) if backend == 'verilator' else None
    library_tool = shutil.which('vlib') if backend == 'modelsim' else None
    rows, commands = [], []

    def execute(argv, cwd, log, budget):
        try:
            result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=max(0.001, budget), check=False)
            value = {'returncode': result.returncode, 'output': result.stdout+result.stderr}
        except (OSError, subprocess.TimeoutExpired) as exc:
            value = {'returncode': None, 'output': '', 'execution_error': type(exc).__name__+': '+str(exc)}
        log.write_text(value['output'], encoding='utf-8')
        commands.append({'argv': argv, 'cwd': str(cwd), 'returncode': value['returncode']})
        return value

    version = execute([compiler, '-V' if backend == 'icarus' else '--version' if backend == 'verilator' else '-version'], run,
                      run/(backend+'-version.log'), 15) if compiler else {'output': backend+' not on PATH'}
    build_toolchain = {}
    if backend == 'verilator':
        for name, tool in [('make', make), ('cxx', shutil.which('g++'))]:
            checked = execute([tool,'--version'],run,run/(name+'-version.log'),15) if tool else {'output':'not on PATH'}
            build_toolchain[name] = {'path':tool, 'version_output':checked['output']}
    uvm_root = (uvm_root or Path(__file__).resolve().parents[1]/'.tools/uvm-core-2020.3.1').resolve()
    uvm_info = {'status': 'not_requested'}
    if any(c['kind'] == 'uvm-probe' for c in selected):
        lock = json.loads((suite/'uvm.lock.json').read_text(encoding='utf-8'))
        try:
            locked = verify_uvm(uvm_root, lock)
            for name in locked:
                target = run/'uvm'/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(local_file(uvm_root, name).read_bytes().replace(b'\r\n', b'\n'))
            verify_uvm(run/'uvm', lock)
            uvm_info = {'status': 'verified_source', 'commit': lock['commit'], 'tag': lock['tag'], 'files': locked}
        except (OSError, ValueError) as exc:
            uvm_info = {'status': 'blocked', 'reason': str(exc)}
    for case in selected:
        directory = run/case['id']
        directory.mkdir()
        (directory/'vectors.hex').write_text(''.join(f'{a:02x}{b:02x}\n' for a,b in vectors()), encoding='ascii')
        observation = {'backend': backend}
        if not compiler or not runtime or backend == 'modelsim' and not library_tool:
            observation['blocked'] = backend+' runtime toolchain not on PATH'
        elif backend == 'verilator' and (not make or not build_toolchain['cxx']['path']):
            observation['blocked'] = 'Verilator simulation requires make and a C++20-capable g++ toolchain on PATH'
        elif case['kind'] == 'uvm-probe' and uvm_info['status'] != 'verified_source':
            observation['blocked'] = 'Official pinned UVM source unavailable: '+uvm_info.get('reason', '')
        else:
            deadline = time.monotonic()+timeout
            sources = [str(path) for path in case_sources(run/'suite', case)]
            includes, defines = [], []
            if case['kind'] == 'uvm-probe':
                includes = [str(run/'uvm/src')]
                defines = ['UVM_NO_DPI']
                sources.insert(0, str(run/'uvm/src/uvm_pkg.sv'))
            job = {'sources': sources, 'includes': includes, 'defines': defines,
                   'pyslang_version': contract['standards']['pyslang_version'], 'output': str(directory/'slang.json')}
            (directory/'slang-job.json').write_text(json.dumps(job), encoding='utf-8')
            checked = execute([sys.executable, '-m', 'fpga_agent.sv_slang', str(directory/'slang-job.json')],
                              directory, directory/'slang.log', deadline-time.monotonic())
            if checked['returncode'] != 0 or not (directory/'slang.json').is_file():
                observation['error'] = 'Independent frontend worker failed'
            else:
                observation['slang'] = json.loads((directory/'slang.json').read_text(encoding='utf-8'))
                if backend == 'icarus':
                    argv = [compiler, '-g2012', '-s', 'tb', '-o', str(directory/'sim.vvp'),
                            *['-I'+p for p in includes], *['-D'+d for d in defines], *sources]
                    runtime_argv = [runtime, str(directory/'sim.vvp')]
                elif backend == 'modelsim':
                    observation['library'] = execute([library_tool, str(directory/'work')], directory,
                                                     directory/'library.log', deadline-time.monotonic())
                    argv = [compiler, '-sv', '-work', str(directory/'work'),
                            *['+incdir+'+p for p in includes], *['+define+'+d for d in defines], *sources]
                    runtime_argv = [runtime, '-c', '-novopt', '-lib', str(directory/'work'), 'tb',
                                    '-do', 'onerror {quit -code 1 -force}; run -all; quit -code 0 -force']
                else:
                    binary = 'sim.exe' if os.name == 'nt' else 'sim'
                    argv = [compiler, '--cc', '--exe', '--main', '--timing', '--assert', '--language', '1800-2012',
                            '--top-module', 'tb', '--prefix', 'Vtb', '-Wno-fatal',
                            '-CFLAGS', '-std=c++20 -O0',
                            '--Mdir', str(directory/'obj'), '-o', binary,
                            *['-I'+p for p in includes], *['-D'+d for d in defines], *sources]
                    runtime_argv = [str(directory/'obj'/binary)]
                if backend == 'modelsim' and observation['library']['returncode'] != 0:
                    observation['error'] = 'Simulation library creation failed'
                else:
                    observation['compile'] = execute(argv, directory, directory/'compile.log', deadline-time.monotonic())
                    if observation['compile']['returncode'] == 0 and case['kind'] != 'reject':
                        if backend == 'verilator':
                            observation['build'] = execute([make,'-C',str(directory/'obj'),'-f','Vtb.mk','-j2'],
                                                          directory,directory/'build.log',deadline-time.monotonic())
                        if backend != 'verilator' or observation['build']['returncode'] == 0:
                            observation['runtime'] = execute(runtime_argv, directory, directory/'runtime.log', deadline-time.monotonic())
                        if backend == 'verilator':
                            observation['executable'] = 'obj/'+binary
                            observation['generated_files'] = {p.relative_to(directory).as_posix():sha(p)
                                                              for p in (directory/'obj').rglob('*') if p.is_file()}
                        if backend == 'modelsim':
                            observation['compiled_library'] = {
                                p.relative_to(directory).as_posix(): sha(p)
                                for p in (directory/'work').rglob('*') if p.is_file()}
        (directory/'observation.json').write_text(json.dumps(observation, indent=2)+'\n', encoding='utf-8')
        status, reason = classify(case, observation)
        rows.append({'id': case['id'], 'status': status, 'reason': reason, 'required': case['required'],
                     'frontend_status': observation.get('slang', {}).get('status', 'not_run'),
                     'observation': (directory/'observation.json').relative_to(output.parent).as_posix()})
    status, baseline, complete = _aggregate(contract, rows)
    if suite_inputs(suite) != inputs:
        status = 'fail'
    report = {'schema_version': SCHEMA, 'created_at': datetime.now(timezone.utc).isoformat(),
              'status': status, 'profile': contract['profile'], 'scope': contract['scope'],
              'baseline_status': baseline, 'all_baseline_cases_selected': complete,
              'uvm_fixture_profile_status': _uvm_profile_status(contract, rows),
              'full_sv_uvm_complete': False, 'uvm_minimum_complete': False,
              'backend': backend, 'verification_model': verification_model(backend),
              'standards': contract['standards'], 'inputs': inputs, 'requested_cases': [c['id'] for c in selected],
              'run_directory': run.relative_to(output.parent).as_posix(), 'cases': rows, 'features': _features(contract, rows),
              'runtime_compiler_version': version['output'].splitlines()[0] if version['output'] else '',
              'build_toolchain': build_toolchain,
              'runtime_mode': 'two-state default; timing/assert enabled' if backend=='verilator' else 'four-state event simulation',
              'uvm_source': uvm_info, 'commands': commands,
              'artifacts': {p.relative_to(output.parent).as_posix(): sha(p) for p in run.rglob('*') if p.is_file()}}
    pending = output.with_name(output.name+'.'+uuid.uuid4().hex+'.tmp')
    pending.write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    pending.replace(output)
    return report


def conformance_valid(report_path: Path, *, suite: Path | None = None) -> tuple[bool, str]:
    """Validate evidence for a bounded run, including partial capability results."""
    try:
        report_path = report_path.resolve()
        report = json.loads(report_path.read_text(encoding='utf-8'))
        suite = (suite or default_suite()).resolve()
        contract = load_contract(suite)
        if report['schema_version'] != SCHEMA or report['inputs'] != suite_inputs(suite):
            return False, 'Conformance contract, fixture or independent checker changed'
        backend = report['backend']
        if backend not in BACKENDS or report['verification_model'] != verification_model(backend):
            return False, 'Unknown or altered runtime backend claim'
        if report['profile'] != contract['profile']:
            return False, 'Capability profile changed'
        mode = 'two-state default; timing/assert enabled' if backend=='verilator' else 'four-state event simulation'
        if report['runtime_mode'] != mode:
            return False, 'Runtime semantics mode changed'
        if report['full_sv_uvm_complete'] is not False or report['uvm_minimum_complete'] is not False:
            return False, 'Unsupported completeness claim'
        requested = report['requested_cases']
        selected = [c for c in contract['cases'] if c['id'] in requested]
        if requested != [c['id'] for c in selected] or not selected or len(report['cases']) != len(selected):
            return False, 'Incomplete or duplicated case inventory'
        artifacts = report['artifacts']
        if not artifacts or any(sha(local_file(report_path.parent, name)) != digest for name,digest in artifacts.items()):
            return False, 'Conformance artifact changed'
        run = local_file(report_path.parent, report['run_directory']+'/suite/contract.json').parent.parent
        version_log = run/(backend+'-version.log')
        if version_log.is_file():
            lines = version_log.read_text(encoding='utf-8').splitlines()
            if (version_log.relative_to(report_path.parent).as_posix() not in artifacts
                    or report['runtime_compiler_version'] != (lines[0] if lines else '')):
                return False, 'Runtime compiler version differs from transcript'
        elif report['runtime_compiler_version'] != backend+' not on PATH':
            return False, 'Runtime compiler version transcript missing'
        if backend == 'verilator':
            for name in ('make','cxx'):
                tool = report['build_toolchain'][name]
                path = run/(name+'-version.log')
                if tool['path']:
                    if (path.relative_to(report_path.parent).as_posix() not in artifacts
                            or path.read_text(encoding='utf-8') != tool['version_output']):
                        return False, 'C++ build tool version transcript differs'
                elif tool['version_output'] != 'not on PATH':
                    return False, 'Missing C++ build tool version transcript'
        elif report['build_toolchain'] != {}:
            return False, 'Unexpected C++ build toolchain claim'
        for name,digest in report['inputs'].items():
            if not name.startswith('@'):
                path = run/'suite'/name
                if path.relative_to(report_path.parent).as_posix() not in artifacts or sha(path) != digest:
                    return False, 'Missing or changed contract/fixture snapshot'
        if report['uvm_source']['status'] == 'verified_source':
            lock = json.loads((suite/'uvm.lock.json').read_text(encoding='utf-8'))
            if verify_uvm(run/'uvm', lock) != report['uvm_source']['files'] or report['uvm_source']['commit'] != lock['commit']:
                return False, 'Official UVM identity changed'
        for case, row in zip(selected, report['cases']):
            if row['id'] != case['id'] or row['observation'] not in artifacts:
                return False, 'Case evidence missing'
            observation = json.loads(local_file(report_path.parent,row['observation']).read_text(encoding='utf-8'))
            if observation.get('backend') != backend:
                return False, 'Observation belongs to a different runtime backend'
            directory = run/case['id']
            if row['observation'] != (directory/'observation.json').relative_to(report_path.parent).as_posix():
                return False, 'Observation refers to another case'
            needed = [directory/'vectors.hex']
            if 'library' in observation:
                needed += [directory/'library.log']
                if (directory/'library.log').read_text(encoding='utf-8') != observation['library']['output']:
                    return False, 'Library creation transcript differs from observation'
            if 'compile' in observation:
                needed += [directory/n for n in ('slang-job.json','slang.json','slang.log','compile.log')]
                sources = case_sources(run/'suite', case)
                if case['kind'] == 'uvm-probe':
                    sources.insert(0, run/'uvm/src/uvm_pkg.sv')
                names = list(map(str, sources))
                job = json.loads((directory/'slang-job.json').read_text(encoding='utf-8'))
                compile_commands = [command for command in report['commands']
                                    if command['cwd'] == str(directory) and command['argv'][-len(names):] == names]
                if job['sources'] != names or len(compile_commands) != 1:
                    return False, 'Compiler inputs disagree with the declared case sources'
                if (directory/'compile.log').read_text(encoding='utf-8') != observation['compile']['output']:
                    return False, 'Compiler transcript differs from observation'
                if json.loads((directory/'slang.json').read_text(encoding='utf-8')) != observation['slang']:
                    return False, 'Frontend result differs from observation'
            if 'build' in observation:
                needed += [directory/'build.log',directory/'obj/Vtb.mk']
                if backend != 'verilator' or (directory/'build.log').read_text(encoding='utf-8') != observation['build']['output']:
                    return False, 'Generated simulation build transcript differs'
                generated = observation['generated_files']
                actual = {p.relative_to(directory).as_posix():sha(p) for p in (directory/'obj').rglob('*') if p.is_file()}
                if generated != actual or not any(n.endswith('.cpp') for n in generated):
                    return False, 'Generated C++ simulation inventory changed'
                needed += [local_file(directory,n) for n in generated]
            if 'runtime' in observation:
                needed += [directory/'runtime.log']
                if backend == 'verilator':
                    if observation['executable'] not in ('obj/sim','obj/sim.exe') or 'build' not in observation:
                        return False, 'Generated simulation executable missing'
                    needed += [directory/observation['executable']]
                else:
                    needed += [directory/('sim.vvp' if backend=='icarus' else 'work/_info')]
                if backend == 'modelsim':
                    library = observation['compiled_library']
                    actual = {p.relative_to(directory).as_posix(): sha(p)
                              for p in (directory/'work').rglob('*') if p.is_file()}
                    if library != actual or 'work/_info' not in library or not any(n.endswith('.qdb') for n in library):
                        return False, 'Compiled ModelSim library inventory changed or missing'
                    needed += [local_file(directory, n) for n in library]
                if (directory/'runtime.log').read_text(encoding='utf-8') != observation['runtime']['output']:
                    return False, 'Runtime transcript differs from observation'
            if any(p.relative_to(report_path.parent).as_posix() not in artifacts for p in needed):
                return False, 'Required case evidence omitted'
            if row['frontend_status'] != observation.get('slang', {}).get('status', 'not_run'):
                return False, 'Frontend capability result changed'
            status, reason = classify(case, observation)
            if (row['status'], row['reason'], row['required']) != (status, reason, case['required']):
                return False, 'Observation or independent oracle disagrees with capability result'
        if _aggregate(contract, report['cases']) != (report['status'], report['baseline_status'], report['all_baseline_cases_selected']):
            return False, 'Conformance summary differs from observed cases'
        if report['uvm_fixture_profile_status'] != _uvm_profile_status(contract, report['cases']):
            return False, 'UVM fixture profile summary differs from observed cases'
        if report['features'] != _features(contract, report['cases']) or report['standards'] != contract['standards'] or report['scope'] != contract['scope']:
            return False, 'Capability/standard/scope summary changed'
        return True, 'Bounded capability observations rechecked; this does not certify complete SV/UVM support'
    except (OSError, ValueError, KeyError, TypeError):
        return False, 'Conformance report missing or malformed'
