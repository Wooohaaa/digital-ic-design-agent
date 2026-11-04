"""Evidence-driven engineering reviews. A model's opinion cannot grant a gate."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .evidence import hdl_input_hashes, implementation_valid, sha, simulation_valid
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


def lint_project(project: Path, timeout: int = 120) -> dict:
    project = project.resolve()
    manifest = load_manifest(project)
    inputs = lint_inputs(project, manifest)
    tool = shutil.which('verilator')
    report = {'schema_version':1,'status':'blocked','tool':'Verilator',
              'started_at':datetime.now(timezone.utc).isoformat(),'inputs':inputs,
              'command':[],'returncode':None,'output':'Verilator is not installed or not on PATH.'}
    if tool:
        command = [tool,'--lint-only','-Wall','--top-module',manifest['top_module'],
                   '-I'+str(project), *[str(project / name) for name in manifest['rtl_sources']]]
        report['command'] = command
        try:
            version = subprocess.run([tool,'--version'],capture_output=True,text=True,timeout=15,check=False)
            report['tool_version'] = (version.stdout+version.stderr).strip()
            result = subprocess.run(command,cwd=project,capture_output=True,text=True,timeout=timeout,check=False)
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

    docs = ['spec/brief.md','spec/design.md']
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
    if cpu:
        traceable = valid_sim
        detail = f"Independent retirement/store/memory comparison: {simulation.get('case_count',0)} cases, {simulation.get('seeds',0)} random seeds."
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
    add('DV-003','verification','pass' if cpu and valid_sim else 'blocked',
        'Trusted reference and harness are fingerprinted.' if cpu else 'RTL and testbench share the generation path; independent verification has not been established.',
        '' if cpu and valid_sim else 'Review the verification plan and supply an independent reference/checker.',['reports/simulation.json'])
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
    add('HW-001','integration','not_run','Physical board programming and measurement have not been performed.',
        'Program the board and record observed outputs against the hardware acceptance procedure.',required=False)
    artifact_ready = valid_sim and (scope == 'simulation' or valid_td)
    report = {'schema_version':1,'created_at':datetime.now(timezone.utc).isoformat(),
              'project':manifest.get('project',project.name),'scope':scope,
              'artifact_ready':artifact_ready,'engineering_signoff':'not_granted',
              'verification_profile':'trusted-rv32i-differential' if cpu else 'generated-self-check',
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
