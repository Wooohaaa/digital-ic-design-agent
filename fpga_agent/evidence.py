"""Content-addressed evidence gates for simulation, TD and delivery."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path


EVIDENCE_VERSION = 2


def local_file(project: Path, name: str) -> Path:
    path = (project / name).resolve()
    if project.resolve() not in path.parents or not path.is_file():
        raise ValueError(f'input outside project or missing: {name}')
    return path


def hdl_input_hashes(project: Path, names: list[str], *, allow_dynamic_memory: bool = False) -> dict[str, str]:
    """Hash transitive literal includes and ROM images, not only the compilation units."""
    result: dict[str, str] = {}

    def visit(path: Path) -> None:
        path = local_file(project, str(path))
        name = path.relative_to(project.resolve()).as_posix()
        if name in result:
            return
        result[name] = sha(path)
        if path.suffix.lower() not in {'.v', '.sv', '.vh', '.svh'}:
            return
        text = re.sub(r'/\*.*?\*/|//[^\r\n]*', ' ', path.read_text(encoding='utf-8'), flags=re.S)
        includes = re.findall(r'`\s*include\s+([^\r\n]+)', text)
        for include in includes:
            match = re.fullmatch(r'"([^"\r\n]+)"\s*', include)
            if not match:
                raise ValueError(f'non-literal include cannot be fingerprinted: {name}')
            relative = match.group(1)
            candidate = path.parent / relative
            visit(candidate if candidate.is_file() else project / relative)
        for argument in re.findall(r'\$readmem[hb]\s*\(\s*([^,]+),', text):
            match = re.fullmatch(r'"([^"\r\n]+)"\s*', argument)
            if match:
                visit(project / match.group(1))
            elif not allow_dynamic_memory:
                raise ValueError(f'dynamic memory image cannot be fingerprinted: {name}')

    for name in names:
        visit(project / name)
    return result


def simulation_input_hashes(project: Path, manifest: dict) -> dict[str, str]:
    trusted_cpu = manifest.get('verification', {}).get('kind') == 'riscv-retire'
    result = hdl_input_hashes(project, manifest['rtl_sources'] + manifest['testbench_sources'],
                              allow_dynamic_memory=trusted_cpu)
    for name in ('spec/brief.md', 'spec/design.md', 'spec/verification-plan.json'):
        if (project / name).is_file():
            result[name] = sha(local_file(project, name))
    # Mixed hardware/software workloads depend on firmware, checkpoints and host
    # checkers as well as HDL. Resolve every declared dependency inside project.
    extra=manifest.get('verification',{}).get('additional_inputs',[])
    if not isinstance(extra,list) or any(not isinstance(name,str) for name in extra):
        raise ValueError('verification.additional_inputs must be a list of local paths')
    for name in extra:
        path=local_file(project,name)
        result[path.relative_to(project.resolve()).as_posix()]=sha(path)
    config = {key: manifest.get(key) for key in ('top_module', 'testbench_top', 'rtl_sources',
              'testbench_sources', 'verification', 'checks', 'board_signature', 'trusted_testbench_hashes')}
    result['@simulation_config'] = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    return result


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes(project: Path, manifest: dict) -> dict[str,str]:
    result={}
    for name in manifest['rtl_sources']+manifest['testbench_sources']:
        path=(project/name).resolve()
        if project.resolve() not in path.parents:raise ValueError(f'input outside project: {name}')
        result[Path(name).as_posix()]=sha(path)
    return result


def input_hashes(project: Path,manifest: dict) -> dict[str,str]:
    # Testbenches belong to simulation evidence, not to the synthesized netlist.
    # A stronger verifier can be rerun without rebuilding unchanged hardware.
    result=hdl_input_hashes(project, manifest['rtl_sources'])
    for name in [manifest['board_constraints'],manifest['timing_constraints'],'board/hx1p35a.json']:
        path=(project/name).resolve()
        if project.resolve() not in path.parents:raise ValueError(f'input outside project: {name}')
        result[Path(name).as_posix()]=sha(path)
    keys=('device_family','device','top_module','testbench_top','board_io','verification','board_signature','rtl_sources')
    config={key:manifest.get(key) for key in keys}
    result['@flow_config']=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
    return result


def simulation_valid(project: Path,manifest: dict,report: dict) -> tuple[bool,str]:
    if not isinstance(report, dict):return False,'simulation report is not an object'
    if report.get('status')!='pass':return False,'simulation has not passed'
    if report.get('source_hashes')!=source_hashes(project,manifest):return False,'HDL/testbench changed since simulation'
    if report.get('evidence_version') != EVIDENCE_VERSION:return False,'simulation evidence schema changed; rerun verification'
    if report.get('verification_inputs') != simulation_input_hashes(project,manifest):return False,'verification configuration, specification or dependencies changed'
    if type(report.get('returncode')) is not int or report['returncode'] != 0:return False,'simulator exit status is not successful'
    if manifest.get('verification',{}).get('kind')=='hdmi-cube':
        from .video import evidence_valid
        return evidence_valid(project,manifest,report)
    if manifest.get('verification',{}).get('kind')=='tinystories-fp32':
        from .tinyllm import evidence_valid
        return evidence_valid(project,manifest,report)
    if manifest.get('verification',{}).get('kind')=='riscv-retire':
        from .cpu_bench import trusted_hashes
        if report.get('verification_kind')!='riscv-retire':return False,'trusted RV32I differential verification missing'
        if report.get('trusted_harness_hashes')!=trusted_hashes(project):return False,'reference/harness changed since verification'
        if report.get('board_soc')!='pass':return False,'board ROM/RAM integration has not passed'
        if report.get('seeds')!=manifest['verification']['seeds']:return False,'requested seed count differs from evidence'
        if type(report.get('seeds')) is not int or not 0<=report['seeds']<=1024:return False,'invalid CPU regression seed count'
        from .riscv import corpus, run_reference
        expected = [case.name for case in corpus(report['seeds'])]
        cases = report.get('cases')
        if not isinstance(cases,list) or report.get('case_count') != len(expected):return False,'CPU regression case count incomplete'
        if [case.get('name') for case in cases if isinstance(case,dict)] != expected:return False,'CPU regression case inventory differs'
        if any(case.get('status') != 'pass' for case in cases):return False,'CPU regression contains failed cases'
        required = set().union(*(run_reference(case).coverage for case in corpus(0)))
        if not required.issubset(set(report.get('instruction_coverage',[]))):return False,'CPU directed functional coverage incomplete'
    else:
        runtime = report.get('runtime_output')
        if not isinstance(runtime,str) or not re.search(r'(?m)^\s*PASS(?:\b|:)',runtime):return False,'runtime PASS evidence missing'
        if re.search(r'(?im)^\s*(?:FAIL|FATAL|ERROR)(?:\b|:)',runtime):return False,'runtime contains assertion or simulator errors'
        plan_path = project / 'spec/verification-plan.json'
        if plan_path.is_file():
            try:
                plan=json.loads(plan_path.read_text(encoding='utf-8'))
                requirements=plan.get('requirements') if isinstance(plan,dict) else None
                if not isinstance(requirements,list) or not requirements:return False,'verification plan requirements missing'
                ids=[item.get('id') for item in requirements if isinstance(item,dict)]
                if len(ids)!=len(requirements) or any(not isinstance(item,str) or not re.fullmatch(r'REQ-[0-9]{3,}',item) for item in ids) or len(set(ids))!=len(ids):return False,'verification plan IDs invalid'
                markers=re.findall(r'(?m)^\s*CHECK\s+(\S+)\s+PASS\s*$',runtime)
                if set(markers)!=set(ids) or any(markers.count(item)!=1 for item in ids):return False,'planned requirement execution incomplete'
                if report.get('checks')!=[{'id':item,'status':'pass'} for item in ids]:return False,'requirement evidence incomplete'
            except (OSError,ValueError):
                return False,'verification plan invalid'
    return True,'pass'


def implementation_valid(project: Path,manifest: dict,report: dict) -> tuple[bool,str]:
    if not isinstance(report, dict):return False,'implementation report is not an object'
    if report.get('status')!='pass' or report.get('stage')!='full':return False,'TD full implementation has not passed'
    recorded=dict(report.get('input_hashes') or {})
    for name in manifest['testbench_sources']:recorded.pop(name,None)
    if recorded!=input_hashes(project,manifest):return False,'RTL, constraints, board or build configuration changed'
    if not isinstance(report.get('run_directory'),str) or not report['run_directory']:return False,'implementation source snapshot directory missing'
    if report.get('run_directory'):
        run=(project/report['run_directory']).resolve()
        if project.resolve() not in run.parents:return False,'implementation snapshot outside project'
        for name in list(hdl_input_hashes(project,manifest['rtl_sources']))+[manifest['board_constraints'],manifest['timing_constraints']]:
            snapshot=run/'sources'/name
            if not snapshot.is_file() or sha(snapshot)!=recorded.get(name):return False,'implementation source snapshot missing or modified'
        from .td import memory_image_aliases
        for alias,source in memory_image_aliases(project,manifest,run).items():
            snapshot=run/alias
            if not snapshot.is_file() or sha(snapshot)!=recorded.get(source):
                return False,'TD runtime memory image missing or modified'
        raw_timing=run/f"{manifest['project']}_full_Runs"/'phy_1/final_timing.rpt'
        if not raw_timing.is_file():return False,'raw final timing report missing'
        from .td import parse_timing_summary
        if parse_timing_summary(raw_timing.read_text(encoding='utf-8',errors='replace')) != report.get('timing_summary'):return False,'timing summary differs from raw tool report'
    timing=report.get('timing_summary') or {}
    import math
    for key in ('setup_wns_ns','hold_wns_ns'):
        value=timing.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:return False,'final timing is missing or not closed'
    for key in ('setup_tns_ns','hold_tns_ns'):
        value=timing.get(key)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value != 0:return False,'final total negative slack is missing or not zero'
    for resource in (report.get('resource_summary') or {}).values():
        if isinstance(resource,dict) and isinstance(resource.get('available'),int):
            if not isinstance(resource.get('used'),int) or resource['used'] > resource['available']:return False,'device resource capacity exceeded'
    bits=report.get('bitstream_hashes') or {}
    if not bits:return False,'bitstream evidence missing'
    for name,expected in bits.items():
        path=(project/name).resolve()
        if project.resolve() not in path.parents or not path.is_file() or path.stat().st_size == 0 or sha(path)!=expected:return False,'bitstream missing or modified'
    if set(report.get('bitstreams') or []) != set(bits):return False,'bitstream inventory differs from fingerprints'
    return True,'pass'
