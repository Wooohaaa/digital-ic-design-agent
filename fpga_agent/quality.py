"""Evidence-driven engineering reviews. A model's opinion cannot grant a gate."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .evidence import hdl_input_hashes, implementation_valid, sha, simulation_input_hashes, simulation_valid, trusted_testbench_valid
from .formal import formal_valid, lec_valid
from .implement import implement_valid
from .project import load_manifest
from .svsim import svsim_valid
from .synth import synth_valid
from .coverage import coverage_valid, functional_coverage_valid
from .cdc import cdc_valid
from .timing import timing_valid


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
        'Review clock/reset domains and exceptions; run `icforge cdc PROJECT` for structural crossing candidates.',['spec/design.md'])
    cdc = read_report(project / 'reports/cdc.json')
    try:
        cdc_ok, cdc_reason = cdc_valid(project, manifest, cdc)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        cdc_ok, cdc_reason = False, str(exc)
    cdc_status = cdc.get('status')
    if cdc_ok:
        add('CDC-001','design','pass',cdc_reason,'',['reports/cdc.json'],required=False)
    elif cdc_status == 'review_required':
        add('CDC-001','design','review_required',cdc_reason,
            'Review the reported clock/reset domain crossing candidates; add synchronizers or document the transfer.',
            ['reports/cdc.json'],required=False)
    elif cdc_status in (None, 'missing', 'invalid'):
        add('CDC-001','design','not_run',
            'No clock/reset domain crossing analysis is recorded.',
            'Run `icforge cdc PROJECT` to scan the RTL for clock/reset domain crossing candidates.',
            ['reports/cdc.json'],required=False)
    else:
        add('CDC-001','design','blocked',cdc_reason,
            'Rerun `icforge cdc PROJECT` on current inputs; inspect reports/cdc.log.',
            ['reports/cdc.json'],required=False)
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
    synth = read_report(project / 'reports/synth.json')
    try:
        synth_ok, synth_reason = synth_valid(project, manifest, synth)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        synth_ok, synth_reason = False, str(exc)
    if synth_ok:
        add('SYNTH-001','implementation','pass',synth_reason,'',['reports/synth.json'],required=False)
    elif synth.get('status') in (None, 'missing', 'invalid'):
        add('SYNTH-001','implementation','not_run',
            'No vendor-neutral synthesis evidence; the RTL has not been shown to synthesize to a gate netlist.',
            'Run `icforge synth PROJECT` to lower the RTL to a technology-independent netlist.',
            ['reports/synth.json'],required=False)
    elif synth.get('status') == 'blocked':
        add('SYNTH-001','implementation','blocked',synth_reason,
            'Install Yosys (or the formal extra) and rerun `icforge synth PROJECT`.',
            ['reports/synth.json'],required=False)
    else:
        add('SYNTH-001','implementation','blocked',
            f"Generic synthesis is not established: {synth_reason}.",
            'Rerun `icforge synth PROJECT` on current inputs and fix any synthesis errors.',
            ['reports/synth.json'],required=False)
    implement = read_report(project / 'reports/implement.json')
    try:
        impl_ok, impl_reason = implement_valid(project, manifest, implement)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        impl_ok, impl_reason = False, str(exc)
    impl_status = implement.get('status')
    if impl_ok:
        add('IMPL-003','implementation','pass',impl_reason,'',['reports/implement.json'],required=False)
    elif impl_status in (None, 'missing', 'invalid'):
        add('IMPL-003','implementation','not_run',
            'No vendor-neutral implementation evidence; no target backend has been run.',
            'Run `icforge implement PROJECT --target TARGET` to place, route and pack the design.',
            ['reports/implement.json'],required=False)
    elif impl_status == 'blocked':
        add('IMPL-003','implementation','blocked',impl_reason,
            'Install the target toolchain (openfpga extra or Anlogic TD) and rerun `icforge implement PROJECT`.',
            ['reports/implement.json'],required=False)
    else:
        add('IMPL-003','implementation','blocked',
            f"Implementation is not established: {impl_reason}.",
            'Inspect reports/implement.log and rerun `icforge implement PROJECT --target TARGET` on current inputs.',
            ['reports/implement.json'],required=False)
    # Vendor-neutral delivery: a non-Anlogic target with bitstream capability
    # satisfies the fpga delivery gate through the open implementation layer
    # instead of the Anlogic TD flow. The generic (synthesis-only) target never
    # claims delivery because it has no bitstream.
    neutral_deliverable = False
    try:
        from .targets import resolve_target
        _profile = resolve_target(manifest)
        neutral_ok, _neutral_reason = implement_valid(project, manifest, implement)
        neutral_deliverable = bool(neutral_ok and _profile.get("capabilities", {}).get("bitstream"))
    except (OSError, ValueError, TypeError, KeyError):
        neutral_deliverable = False
    add('DELIV-001','integration',
        'pass' if neutral_deliverable else 'not_run',
        ('Vendor-neutral bitstream delivery evidence is current for the selected target.'
         if neutral_deliverable
         else 'No vendor-neutral delivery evidence; the Anlogic TD flow remains the delivery path.'),
        ('' if neutral_deliverable
         else 'Run `icforge implement PROJECT` on a bitstream-capable target to enable vendor-neutral delivery.'),
        ['reports/implement.json'], required=False)
    coverage = read_report(project / 'reports/coverage.json')
    try:
        coverage_ok, coverage_reason = coverage_valid(project, manifest, coverage)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        coverage_ok, coverage_reason = False, str(exc)
    coverage_status = coverage.get('status')
    if coverage_ok:
        add('COV-001','verification','pass',coverage_reason,'',['reports/coverage.json'],required=False)
    elif coverage_status in (None, 'missing', 'invalid'):
        add('COV-001','verification','not_run',
            'No RTL structural coverage evidence is recorded.',
            'Run `icforge coverage PROJECT` to measure line/branch/toggle coverage of the RTL.',
            ['reports/coverage.json'],required=False)
    elif coverage_status == 'blocked':
        add('COV-001','verification','blocked',coverage_reason,
            'Extend the testbench to exercise the uncovered points, then rerun `icforge coverage PROJECT`.',
            ['reports/coverage.json'],required=False)
    else:
        add('COV-001','verification','blocked',
            f"RTL structural coverage is not established: {coverage_reason}.",
            'Inspect reports/coverage.log and rerun `icforge coverage PROJECT` on current inputs.',
            ['reports/coverage.json'],required=False)
    # COV-002: functional (cover property) coverage, optional and separate from COV-001.
    try:
        functional_ok, functional_reason = functional_coverage_valid(project, manifest, coverage)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        functional_ok, functional_reason = False, str(exc)
    if functional_ok:
        add('COV-002','verification','pass',functional_reason,'',
            ['reports/coverage.json'],required=False)
    elif coverage.get('functional') in (None, {}):
        if coverage.get('functional_requested'):
            add('COV-002','verification','blocked',functional_reason,
                'Run `icforge coverage PROJECT --functional-coverage` and add `cover property` '
                'points, then rerun.',
                ['reports/coverage.json'],required=False)
        else:
            add('COV-002','verification','not_run',
                'No functional (cover property) coverage evidence is recorded.',
                'Run `icforge coverage PROJECT --functional-coverage` with `cover property` '
                'points in the RTL.',
                ['reports/coverage.json'],required=False)
    elif coverage.get('status') == 'fail' and coverage.get('assertion_failure'):
        add('COV-002','verification','blocked',
            f"Functional coverage not established: {functional_reason}.",
            'A concurrent assertion failed; inspect reports/coverage.log.',
            ['reports/coverage.json'],required=False)
    else:
        add('COV-002','verification','blocked',functional_reason,
            'Extend the testbench to hit the uncovered cover property points, then rerun '
            '`icforge coverage PROJECT --functional-coverage`.',
            ['reports/coverage.json'],required=False)
    add('DV-004','verification','not_run',
        'Post-route simulation and ASIC physical signoff are not evidenced by this flow; '
        'line/branch/toggle structural coverage (COV-001) and functional cover-property '
        'coverage (COV-002) are tracked separately.',
        'Select required analyses in the verification plan and collect tool-produced evidence.',required=False)
    timing = read_report(project / 'reports/timing.json')
    try:
        timing_ok, timing_reason = timing_valid(project, manifest, timing)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        timing_ok, timing_reason = False, str(exc)
    timing_status = timing.get('status')
    if timing_ok:
        add('TIM-001','implementation','pass',timing_reason,'',['reports/timing.json'],required=False)
    elif timing_status in (None, 'missing', 'invalid'):
        add('TIM-001','implementation','not_run',
            'No post-route timing/signoff evidence is recorded.',
            'Run `icforge implement PROJECT` then `icforge timing PROJECT`.',
            ['reports/timing.json'],required=False)
    elif timing_status == 'fail':
        add('TIM-001','implementation','blocked',timing_reason,
            'Fix the timing violation (or the constraint) and rerun the implementation and timing steps.',
            ['reports/timing.json'],required=False)
    else:
        add('TIM-001','implementation','blocked',timing_reason,
            'Post-route simulation needs a target simulator (icesim for iCE40); '
            'install it or set ICFORGE_ICESIM, then rerun `icforge timing PROJECT`.',
            ['reports/timing.json'],required=False)
    mutation = read_report(project / 'reports/mutation.json')
    try:
        mutation_current = (mutation.get('status') == 'complete'
                            and mutation.get('simulation_inputs') == simulation_input_hashes(project, manifest))
        if mutation_current:
            from .mutation import simulator_fingerprint
            mutation_current = mutation.get('simulator_fingerprint') == simulator_fingerprint()
    except (OSError, ValueError, TypeError, KeyError):
        mutation_current = False
    mutation_counts = mutation.get('counts', {}) if isinstance(mutation.get('counts'), dict) else {}
    mutation_rate = mutation.get('observed_kill_rate_percent')
    mutation_review = (not mutation_current or mutation.get('candidate_selection_truncated')
                       or mutation_counts.get('survived', 0) > 0
                       or mutation_counts.get('compile_error', 0) > 0
                       or mutation_counts.get('inconclusive', 0) > 0
                       or mutation_counts.get('timeout', 0) > 0)
    if mutation_current:
        mutation_status = 'review_required' if mutation_review else 'pass'
        mutation_detail = (f"Current mutation audit: {mutation_counts.get('killed', 0)} killed, "
                           f"{mutation_counts.get('survived', 0)} survived, "
                           f"{mutation_counts.get('compile_error', 0)} compile-invalid; "
                           f"observed kill rate {mutation_rate}% (diagnostic only).")
        mutation_action = ('Inspect reports/mutation.json survivors, compile-invalid candidates and inconclusive cases; add only specification-backed checks, '
                           'then rerun the audit.' if mutation_review else '')
    elif mutation.get('status') in {'missing', 'invalid'}:
        mutation_status = 'not_run'
        mutation_detail = 'No current RTL mutation audit is recorded; existing simulation PASS does not measure testbench fault sensitivity.'
        mutation_action = 'Run `icforge mutation-audit PROJECT` after the generic Icarus baseline passes.'
    else:
        mutation_status = 'not_run' if mutation.get('status') == 'blocked' else 'stale'
        mutation_detail = 'Mutation audit is blocked, incomplete or based on different verification inputs.'
        mutation_action = 'Resolve the audit blocker or rerun `icforge mutation-audit PROJECT` on current inputs.'
    add('DV-005','verification',mutation_status,mutation_detail,mutation_action,
        ['reports/mutation.json'],required=False)
    lec = read_report(project / 'reports/lec.json')
    try:
        lec_ok, lec_reason = lec_valid(project, manifest, lec)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        lec_ok, lec_reason = False, str(exc)
    if lec_ok:
        add('DV-006','verification','pass',lec_reason,'',['reports/lec.json'],required=False)
    elif lec.get('status') in (None, 'missing', 'invalid'):
        add('DV-006','verification','not_run',
            'No current logic-equivalence evidence; RTL-versus-netlist equivalence is unproven.',
            'Run `icforge lec PROJECT` to prove the RTL equals its technology-independent netlist.',
            ['reports/lec.json'],required=False)
    elif lec.get('status') == 'blocked':
        add('DV-006','verification','blocked',lec_reason,
            'Install Yosys (or the formal extra) and rerun `icforge lec PROJECT`.',
            ['reports/lec.json'],required=False)
    else:
        add('DV-006','verification','blocked',
            f"Logic equivalence is not established: {lec_reason}.",
            'Inspect reports/lec.log unproven $equiv cells, fix the divergence and rerun `icforge lec PROJECT`.',
            ['reports/lec.json'],required=False)
    formal = read_report(project / 'reports/formal.json')
    try:
        formal_ok, formal_reason = formal_valid(project, manifest, formal)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        formal_ok, formal_reason = False, str(exc)
    formal_status = formal.get('status')
    if formal_ok:
        add('DV-007','verification','pass',formal_reason,'',['reports/formal.json'],required=False)
    elif formal_status == 'not_applicable':
        add('DV-007','verification','not_run',
            'The RTL declares no assertions or covers, so no formal property could be checked.',
            'Add specification-backed immediate/concurrent assertions and rerun `icforge formal PROJECT`.',
            ['reports/formal.json'],required=False)
    elif formal_status == 'fail':
        add('DV-007','verification','blocked',
            'A counterexample falsifies a declared property.',
            'Inspect reports/formal.log; fix the RTL or the property, then rerun `icforge formal PROJECT`.',
            ['reports/formal.json'],required=False)
    elif formal_status in (None, 'missing', 'invalid'):
        add('DV-007','verification','not_run',
            'No current formal property evidence is recorded.',
            'Run `icforge formal PROJECT` to check declared assertions (bounded BMC or temporal induction).',
            ['reports/formal.json'],required=False)
    elif formal_status == 'blocked':
        add('DV-007','verification','blocked',formal_reason,
            'Install Yosys (or the formal extra) and rerun `icforge formal PROJECT`.',
            ['reports/formal.json'],required=False)
    else:
        add('DV-007','verification','blocked',
            f"Formal property evidence is not established: {formal_reason}.",
            'Rerun `icforge formal PROJECT` on current inputs.',
            ['reports/formal.json'],required=False)
    svsim = read_report(project / 'reports/svsim.json')
    try:
        svsim_ok, svsim_reason = svsim_valid(project, manifest, svsim)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        svsim_ok, svsim_reason = False, str(exc)
    svsim_status = svsim.get('status')
    if svsim_ok:
        add('DV-008','verification','pass',svsim_reason,'',['reports/svsim.json'],required=False)
    elif svsim_status in (None, 'missing', 'invalid'):
        add('DV-008','verification','not_run',
            'No class-based (UVM-style) environment evidence is recorded.',
            'Run `icforge svsim PROJECT` to elaborate and execute the class environment with Verilator.',
            ['reports/svsim.json'],required=False)
    elif svsim_status == 'blocked':
        add('DV-008','verification','blocked',svsim_reason,
            'Install Verilator with a C++ compiler and make, then rerun `icforge svsim PROJECT`.',
            ['reports/svsim.json'],required=False)
    else:
        add('DV-008','verification','blocked',
            f"Class-environment evidence is not established: {svsim_reason}.",
            'Inspect reports/svsim.log; fix the environment or RTL and rerun `icforge svsim PROJECT`.',
            ['reports/svsim.json'],required=False)
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
    artifact_ready = valid_sim and (scope == 'simulation' or valid_td or neutral_deliverable) and (not hardware_required or hardware_ok)
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
