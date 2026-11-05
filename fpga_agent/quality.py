"""Evidence-driven engineering reviews. A model's opinion cannot grant a gate."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .evidence import hdl_input_hashes, implementation_valid, sha, simulation_valid, trusted_testbench_valid
from .project import load_manifest


def read_report(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {'status':'invalid','error':'report must be an object'}
    except (OSError, ValueError) as exc:
        return {'status':'missing' if not path.exists() else 'invalid','error':str(exc)}


def write_report(project: Path, name: str, report: dict) -> None:
    directory = project / 'reports'
    directory.mkdir(exist_ok=True)
    path = directory / name
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    os.replace(temporary, path)


def lint_inputs(project: Path, manifest: dict) -> dict:
    result = hdl_input_hashes(project, manifest['rtl_sources'])
    result['@top_module'] = manifest['top_module']
    result['@checker'] = sha(Path(__file__))
    return result


def _verilator_command() -> list[str]:
    """Resolve native Verilator or its MSYS2 Perl launcher on Windows."""
    executable = shutil.which('verilator')
    if executable:
        return [executable]
    if os.name != 'nt':
        return []
    for entry in os.environ.get('PATH', '').split(os.pathsep):
        if not entry:
            continue
        launcher = Path(entry) / 'verilator'
        try:
            with launcher.open(encoding='utf-8', errors='replace') as stream:
                is_perl_launcher = launcher.is_file() and 'perl' in stream.readline().lower()
        except OSError:
            is_perl_launcher = False
        if not is_perl_launcher:
            continue
        native_binary = Path(entry) / 'verilator_bin.exe'
        if native_binary.is_file():
            return [str(native_binary)]
        perl = shutil.which('perl.exe') or shutil.which('perl')
        if not perl:
            # MSYS2 stores Perl beside the `usr` tree while Verilator's script
            # lives under a subsystem's `bin` directory (e.g. ucrt64/bin).
            candidate = Path(entry).parent.parent / 'usr' / 'bin' / 'perl.exe'
            if candidate.is_file():
                perl = str(candidate)
        if perl:
            return [perl, str(launcher)]
    return []


def _verilator_environment(command: list[str]) -> dict[str, str]:
    env = dict(os.environ)
    for item in command:
        candidate = Path(item)
        if candidate.name.lower() not in {'verilator', 'verilator.exe', 'verilator_bin.exe'}:
            continue
        root = candidate.parent.parent / 'share' / 'verilator'
        if root.is_dir():
            env['VERILATOR_ROOT'] = str(root)
            break
    return env


def lint_project(project: Path, timeout: int = 120) -> dict:
    project = project.resolve()
    manifest = load_manifest(project)
    inputs = lint_inputs(project, manifest)
    tool = _verilator_command()
    report = {'schema_version':1,'status':'blocked','tool':'Verilator',
              'started_at':datetime.now(timezone.utc).isoformat(),'inputs':inputs,
              'command':[],'returncode':None,'output':'Verilator is not installed or not on PATH.'}
    if tool:
        command = tool + ['--lint-only','-Wall','--top-module',manifest['top_module'],
                   '-I'+str(project), *[str(project / name) for name in manifest['rtl_sources']]]
        report['command'] = command
        tool_env = _verilator_environment(tool)
        try:
            version = subprocess.run(tool+['--version'],capture_output=True,text=True,timeout=15,check=False,env=tool_env)
            report['tool_version'] = (version.stdout+version.stderr).strip()
            result = subprocess.run(command,cwd=project,capture_output=True,text=True,timeout=timeout,check=False,env=tool_env)
            report.update(returncode=result.returncode,output=result.stdout+result.stderr,
                          status='pass' if result.returncode == 0 else 'fail')
            if lint_inputs(project,load_manifest(project)) != inputs:
                report.update(status='fail',output=report['output']+'\nInputs changed during lint.')
        except (OSError,subprocess.TimeoutExpired) as exc:
            report.update(status='fail',output=str(exc))
    write_report(project,'lint.json',report)
    (project / 'reports/lint.log').write_text(report['output'],encoding='utf-8')
    return report


def review_project(project: Path, scope: str = 'fpga', *, write: bool = True) -> dict:
    """Report artifact readiness separately from unresolved engineering signoff work."""
    if scope not in {'simulation','fpga'}:
        raise ValueError('review scope must be simulation or fpga')
    project = project.resolve()
    manifest = load_manifest(project)
    simulation = read_report(project / 'reports/simulation.json')
    implementation = read_report(project / 'reports/td.json')
    lint = read_report(project / 'reports/lint.json')
    gates = []

    def add(identifier, owner, status, detail, action, evidence=None, required=True):
        gates.append({'id':identifier,'owner':owner,'status':status,'detail':detail,
                      'next_action':action,'evidence':evidence or [],'required':required})

    docs = ['SPEC.md'] if manifest.get('verification',{}).get('kind')=='hdmi-cube' else ['spec/brief.md','spec/design.md']
    missing = [name for name in docs if not (project/name).is_file() or not (project/name).read_text(encoding='utf-8').strip()]
    add('SPEC-001','requirements','blocked' if missing else 'pass',
        'Missing specification: '+', '.join(missing) if missing else 'Requirements and design description are recorded.',
        'Record original requirements, assumptions and acceptance criteria.' if missing else '',docs)
    try:
        valid_sim, reason = simulation_valid(project,manifest,simulation)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        valid_sim, reason = False,str(exc)
    add('DV-001','verification','pass' if valid_sim else 'blocked',reason,
        '' if valid_sim else 'Run /verify and resolve the first failing counterexample.',['reports/simulation.json'])
    cpu = manifest.get('verification',{}).get('kind') == 'riscv-retire'
    tinyllm = manifest.get('verification',{}).get('kind') == 'tinystories-fp32'
    hdmi = manifest.get('verification',{}).get('kind') == 'hdmi-cube'
    trusted_hdl = manifest.get('verification',{}).get('kind') == 'trusted-testbench'
    trusted_hdl_ok, trusted_hdl_detail = trusted_testbench_valid(project,manifest) if trusted_hdl else (False,'')
    independent = cpu or tinyllm or hdmi or (trusted_hdl and trusted_hdl_ok)
    if cpu:
        traceable = valid_sim
        detail = f"Independent retirement/store/memory comparison: {simulation.get('case_count',0)} cases, {simulation.get('seeds',0)} random seeds."
    elif hdmi:
        traceable=valid_sim
        detail='Independent raster, TMDS, 256-pose geometry, framebuffer, synthesizable serializer and top-level video checks.'
    elif trusted_hdl:
        traceable = valid_sim and trusted_hdl_ok
        detail = trusted_hdl_detail
    else:
        plan = read_report(project / 'spec/verification-plan.json')
        requirements = plan.get('requirements',[])
        checks = simulation.get('checks',[])
        requirements = requirements if isinstance(requirements,list) else []
        checks = checks if isinstance(checks,list) else []
        ids = [item.get('id') for item in requirements if isinstance(item,dict)]
        traceable = valid_sim and bool(ids) and all(isinstance(identifier,str) for identifier in ids) and len(ids) == len(requirements) and len(set(ids)) == len(ids) and checks == [{'id':identifier,'status':'pass'} for identifier in ids]
        detail = f'{len(checks)} runtime checks mapped to {len(requirements)} planned requirements.'
    add('DV-002','verification','pass' if traceable else 'blocked',detail,
        '' if traceable else 'Create spec/verification-plan.json and execute every CHECK REQ-NNN PASS assertion.',['spec/verification-plan.json','reports/simulation.json'])
    add('DV-003','verification','pass' if independent and valid_sim else 'blocked',
        'Trusted reference and harness are fingerprinted.' if independent else 'RTL and testbench share the generation path; independent verification has not been established.',
        '' if independent and valid_sim else 'Review the verification plan and supply an independent reference/checker.',['reports/simulation.json'])
    try:
        valid_lint = lint.get('status') == 'pass' and lint.get('returncode') == 0 and lint.get('inputs') == lint_inputs(project,manifest)
    except (OSError,ValueError,KeyError):
        valid_lint = False
    add('RTL-001','design','pass' if valid_lint else 'blocked',
        'Current Verilator -Wall lint passed.' if valid_lint else 'Current strict RTL lint evidence is missing, stale or failing.',
        '' if valid_lint else 'Install Verilator if unavailable, run /lint, and fix warnings.',['reports/lint.json'])
    add('RTL-002','design','review_required',
        'Clock/reset-domain crossings, reset release and timing exceptions require engineering review.',
        'Review clock/reset domains and exceptions; run CDC/RDC analysis for crossings.',['spec/design.md'])
    valid_td = False
    if scope == 'fpga':
        try:
            valid_td, reason = implementation_valid(project,manifest,implementation)
        except (OSError,ValueError,TypeError,KeyError) as exc:
            valid_td, reason = False,str(exc)
        add('IMPL-001','implementation','pass' if valid_td else 'blocked',reason,
            '' if valid_td else 'Resolve board mappings, run /td and close setup/hold WNS and TNS.',['reports/td.json'])
        coverage = implementation.get('sta_coverage_percent')
        add('IMPL-002','implementation','review_required',
            f'Tool-reported STA coverage: {coverage}%. Coverage and unconstrained paths require review; WNS alone does not establish complete timing constraints.',
            'Inspect unconstrained endpoints, clocks and IO delays; document and review every timing exception.',['reports/td.json'])
        board = read_report(project / 'board/hx1p35a.json')
        add('BOARD-001','integration','review_required',str(board.get('confidence','Board revision and schematic confirmation not recorded.')),
            'Confirm device, board revision, pinout, oscillator and IO voltages against the actual schematic.',['board/hx1p35a.json'])
    add('DV-004','verification','not_run',
        'Code/toggle coverage, formal properties/equivalence and post-route simulation are not evidenced by this flow.',
        'Select required analyses in the verification plan and collect tool-produced evidence.',required=False)
    hardware_ok=False
    hardware_detail='Physical board programming and measurement have not been performed.'
    if tinyllm:
        from .hardware_evidence import hardware_valid
        hardware_ok,hardware_detail=hardware_valid(project,read_report(project/'reports/hardware.json'))
    elif hdmi:
        from .video_hardware import evidence_valid
        hardware_ok,hardware_detail=evidence_valid(project,read_report(project/'reports/hardware-video.json'))
    hardware_required=scope=='fpga' and manifest.get('hardware_acceptance',{}).get('required') is True
    add('HW-001','integration','pass' if hardware_ok else 'blocked' if hardware_required else 'not_run',hardware_detail,
        '' if hardware_ok else 'Program the board and record observed outputs against the hardware acceptance procedure.',
        ['reports/hardware.json','reports/hardware-uart.txt'] if tinyllm else ['reports/hardware-video.json'] if hdmi else [],required=hardware_required)
    artifact_ready = valid_sim and (scope == 'simulation' or valid_td) and (not hardware_required or hardware_ok)
    profile = ('trusted-rv32i-differential' if cpu else 'trusted-tinystories-fp32' if tinyllm else
               'trusted-hdmi-cube' if hdmi else 'trusted-hdl-testbench' if trusted_hdl else 'generated-self-check')
    report = {'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),
              'project':manifest.get('project',project.name),'scope':scope,
              'artifact_ready':artifact_ready,'engineering_signoff':'not_granted',
              'verification_profile':profile,
              'gates':gates,'next_actions':[gate['next_action'] for gate in gates if gate['status'] != 'pass' and gate['next_action']],
              'required_open_items':sum(gate['required'] and gate['status'] != 'pass' for gate in gates)}
    if write:
        write_report(project,'engineering-review.json',report)
        lines = [f"# Engineering review: {report['project']}",'',f"Scope: {scope}; artifact ready: {artifact_ready}; engineering signoff: not granted.",'',
                 '| Gate | Owner | Status | Finding |','|---|---|---|---|']
        for gate in gates:
            detail = gate['detail'].replace('|','/').replace('\n',' ')
            lines.append(f"| {gate['id']} | {gate['owner']} | {gate['status']} | {detail} |")
        lines += ['','## Next actions',''] + [f'- {action}' for action in report['next_actions']]
        (project / 'reports/engineering-review.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return report
