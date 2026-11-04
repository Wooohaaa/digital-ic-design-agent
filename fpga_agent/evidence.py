"""Content-addressed evidence gates for simulation, TD and delivery."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path


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
    result={name:sha(project/name) for name in manifest['rtl_sources']}
    for name in [manifest['board_constraints'],manifest['timing_constraints'],'board/hx1p35a.json']:
        path=(project/name).resolve()
        if project.resolve() not in path.parents:raise ValueError(f'input outside project: {name}')
        result[Path(name).as_posix()]=sha(path)
    keys=('device_family','device','top_module','testbench_top','board_io','verification','board_signature')
    config={key:manifest.get(key) for key in keys}
    result['@flow_config']=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
    return result


def simulation_valid(project: Path,manifest: dict,report: dict) -> tuple[bool,str]:
    if report.get('status')!='pass':return False,'simulation has not passed'
    if report.get('source_hashes')!=source_hashes(project,manifest):return False,'HDL/testbench changed since simulation'
    if manifest.get('verification',{}).get('kind')=='riscv-retire':
        from .cpu_bench import trusted_hashes
        if report.get('verification_kind')!='riscv-retire':return False,'trusted RV32I differential verification missing'
        if report.get('trusted_harness_hashes')!=trusted_hashes(project):return False,'reference/harness changed since verification'
        if report.get('board_soc')!='pass':return False,'board ROM/RAM integration has not passed'
        if report.get('seeds')!=manifest['verification']['seeds']:return False,'requested seed count differs from evidence'
    return True,'pass'


def implementation_valid(project: Path,manifest: dict,report: dict) -> tuple[bool,str]:
    if report.get('status')!='pass' or report.get('stage')!='full':return False,'TD full implementation has not passed'
    recorded=dict(report.get('input_hashes') or {})
    for name in manifest['testbench_sources']:recorded.pop(name,None)
    if recorded!=input_hashes(project,manifest):return False,'RTL, constraints, board or build configuration changed'
    if report.get('run_directory'):
        run=(project/report['run_directory']).resolve()
        if project.resolve() not in run.parents:return False,'implementation snapshot outside project'
        for name in manifest['rtl_sources']+[manifest['board_constraints'],manifest['timing_constraints']]:
            snapshot=run/'sources'/name
            if not snapshot.is_file() or sha(snapshot)!=recorded.get(name):return False,'implementation source snapshot missing or modified'
    timing=report.get('timing_summary') or {}
    import math
    for key in ('setup_wns_ns','hold_wns_ns'):
        value=timing.get(key)
        if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:return False,'final timing is missing or not closed'
    bits=report.get('bitstream_hashes') or {}
    if not bits:return False,'bitstream evidence missing'
    for name,expected in bits.items():
        path=(project/name).resolve()
        if project.resolve() not in path.parents or not path.is_file() or sha(path)!=expected:return False,'bitstream missing or modified'
    return True,'pass'
