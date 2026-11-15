from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import platform
import re
import sys
import zipfile
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .model import generate_payload,ModelOutputError
from .project import create_project, load_manifest, resolve_project, save_manifest, write_model_artifacts
from .td import run_td, parse_timing_summary
from .verify import verify_project
from .evidence import input_hashes, simulation_valid, implementation_valid, sha, hdl_input_hashes, simulation_input_hashes


def _read_brief(project: Path, brief: str | None, brief_file: str | None) -> str:
    if brief and brief_file:
        raise ValueError("--brief 与 --brief-file 只能指定一个。")
    if brief_file:
        return Path(brief_file).read_text(encoding="utf-8")
    if brief:
        return brief
    default = project / "spec" / "brief.md"
    if default.is_file():
        return default.read_text(encoding="utf-8")
    raise ValueError("请使用 --brief、--brief-file，或先填写 spec/brief.md。")


def _board_profile(project: Path) -> str:
    return (project / "board" / "hx1p35a.json").read_text(encoding="utf-8")


def _source_hashes(project: Path, manifest: dict) -> dict[str, str]:
    result = {}
    for name in manifest["rtl_sources"] + manifest["testbench_sources"]:
        result[name] = hashlib.sha256((project / name).read_bytes()).hexdigest()
    return result


def _rtl_matches_simulation(project: Path, manifest: dict, sim_result: dict) -> bool:
    simulated = sim_result.get("source_hashes", {})
    current = _source_hashes(project, manifest)
    return simulated == current


def _hdl_text(project: Path, manifest: dict, key: str, limit: int) -> str:
    chunks = []
    for name in manifest.get(key, []):
        path = project / name
        if path.is_file():
            chunks.append(f"// FILE: {name}\n{path.read_text(encoding='utf-8')}")
    return "\n".join(chunks)[:limit]


def do_design(project: Path, brief: str, repair_rounds: int, provider: str = 'exchange', model: str = 'gpt-6-luna') -> int:
    manifest = load_manifest(project)
    if manifest.get('verification',{}).get('kind')=='riscv-retire':
        raise ValueError('CPU projects use cpu-design with a protected independent verification harness.')
    (project / 'spec').mkdir(exist_ok=True)
    (project / 'spec/brief.md').write_text(brief.rstrip()+'\n',encoding='utf-8')
    frozen_verification = None
    diagnostics = ""
    for attempt in range(repair_rounds + 1):
        try:
            payload = generate_payload(
                brief,
                _board_profile(project),
                current_rtl=_hdl_text(project, manifest, "rtl_sources", 60_000) if attempt else "",
                current_tb=_hdl_text(project, manifest, "testbench_sources", 40_000) if attempt else "",
                diagnostics=diagnostics + ('\nFrozen verification contract (preserve exactly):\n'+json.dumps({key:value for key,value in frozen_verification.items() if key != 'files'},ensure_ascii=False) if frozen_verification is not None else ''),
                provider=provider,model=model,workspace=project,audit_dir=project/'generation'/f'round_{attempt:02}',
            )
            verifier = {key:payload.get(key) for key in ('top_module','testbench_top','board_io','checks')}
            candidate_files = payload.get('files',{})
            if not isinstance(candidate_files,dict) or any(not isinstance(name,str) or not isinstance(content,str) for name,content in candidate_files.items()):
                raise ValueError('Model files must map paths to source strings.')
            verifier['files'] = {name.replace('\\','/'):content.rstrip() for name,content in candidate_files.items() if name.replace('\\','/').startswith('tb/')}
            if frozen_verification is not None and verifier != frozen_verification:
                raise ValueError('Verification contract is frozen after the first accepted candidate. Repair RTL only; do not change testbench, checks, top interface or board mapping.')
            write_model_artifacts(project, payload)
            frozen_verification = verifier
        except (ValueError,ModelOutputError) as exc:
            diagnostics=f'Candidate validation failed before commit: {exc}'
            directory=project/'generation'/f'round_{attempt:02}';directory.mkdir(parents=True,exist_ok=True)
            (directory/'validation-error.txt').write_text(diagnostics,encoding='utf-8')
            if provider=='exchange':
                (project/'generation/repair-request.txt').write_text(brief+'\n'+diagnostics,encoding='utf-8')
                break
            continue
        manifest = load_manifest(project)
        result = verify_project(project)
        print(result.output, end="")
        print(f"\n仿真状态：{result.status}（{result.elapsed_seconds:.3f}s）")
        if result.status == "pass":
            manifest["design_status"] = "simulation_pass"
            save_manifest(project, manifest)
            return 0
        diagnostics = result.output
        if attempt < repair_rounds:
            print(f"仿真失败，开始第 {attempt + 1}/{repair_rounds} 轮 RTL/testbench 修复。", file=sys.stderr)
    manifest["design_status"] = "simulation_fail"
    save_manifest(project, manifest)
    return 1


def do_td(project: Path, stage: str, td_root: str | None, *, update_manifest: bool = True) -> int:
    manifest = load_manifest(project)
    if stage == "full" and not manifest.get("td_full_allowed", True):
        ports = ", ".join(manifest.get("unmapped_top_ports", []))
        print(f"TD 全流程被门禁拦截：这些顶层 IO 尚无 HX1P35A 已核实引脚映射：{ports}。可先运行 --stage synth，补齐板卡映射后再做 full。", file=sys.stderr)
        return 2
    sim = project / "reports" / "simulation.json"
    if not sim.is_file():
        print("TD 实现被门禁拦截：当前工程没有通过仿真的报告。先运行 fpga-agent verify。", file=sys.stderr)
        return 2
    sim_result = json.loads(sim.read_text(encoding="utf-8"))
    valid,reason=simulation_valid(project,manifest,sim_result)
    if not valid:
        print(f"TD gate: {reason}. Run verification again.", file=sys.stderr)
        return 2
    started_at=datetime.now(timezone.utc).isoformat()
    build_inputs=input_hashes(project,manifest)
    code, output, command, run_dir = run_td(project, stage, td_root)
    if input_hashes(project,load_manifest(project))!=build_inputs:
        code=1;output+='\nBuild inputs changed while TD was running; implementation evidence rejected.\n'
    for name in list(hdl_input_hashes(project,manifest['rtl_sources']))+[manifest['board_constraints'],manifest['timing_constraints']]:
        snapshot=run_dir/'sources'/name
        if not snapshot.is_file() or sha(snapshot)!=build_inputs.get(name):
            code=1;output+=f'\nTD source snapshot mismatch: {name}\n'
    from .td import memory_image_aliases
    for alias,source in memory_image_aliases(project,manifest,run_dir).items():
        if not (run_dir/alias).is_file() or sha(run_dir/alias)!=build_inputs.get(source):
            code=1;output+=f'\nTD runtime memory image mismatch: {alias}\n'
    project_name = f"{manifest['project']}_{stage}"
    runs_dir = run_dir / f"{project_name}_Runs"
    bitstreams = [p.relative_to(project).as_posix() for p in runs_dir.glob("phy_1/*.bit")]
    timing_report = runs_dir / "phy_1" / "final_timing.rpt"
    timing = None
    sta_coverage = None
    resources = None
    if timing_report.is_file():
        import re

        timing_text = timing_report.read_text(encoding="utf-8", errors="replace")
        timing = parse_timing_summary(timing_text)
        coverage_match = re.search(r'STA coverage\s*:\s*([0-9.]+)%',timing_text)
        sta_coverage = float(coverage_match.group(1)) if coverage_match else None
    area_report = runs_dir / "phy_1" / f"{project_name}_phy.area"
    if area_report.is_file():
        import re

        area_text = area_report.read_text(encoding="utf-8", errors="replace")
        resources = {}
        for key in ("lut6", "reg", "eram", "dsp", "pad"):
            match = re.search(rf"^#{key}\s+(\d+)\s+out of\s+(\d+)", area_text, re.M)
            if match:
                resources[key] = {"used": int(match.group(1)), "available": int(match.group(2))}
        reg_match = re.search(r"^#reg\s+(\d+)\s*$", area_text, re.M)
        if reg_match:
            resources["reg"] = {"used": int(reg_match.group(1)), "available": None}
    report = {
        "status": "pass" if code == 0 else "fail",
        "stage": stage,
        "command": command,
        "run_directory": run_dir.relative_to(project).as_posix(),
        "bitstreams": bitstreams,
        "timing_summary": timing,
        'sta_coverage_percent':sta_coverage,
        "resource_summary": resources,
        "started_at": started_at,
        "output": output,
        "source_hashes": _source_hashes(project, manifest),
        "input_hashes": build_inputs,
        "bitstream_hashes": {name:sha(project/name) for name in bitstreams},
    }
    report_dir = project / "reports"
    report_dir.mkdir(exist_ok=True)
    (report_dir / "td.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (report_dir / "td.log").write_text(output, encoding="utf-8")
    print(output, end="")
    if code == 0:
        manifest["td_status"] = stage
        if update_manifest:
            save_manifest(project, manifest)
        return 0
    manifest["td_status"] = "fail"
    if update_manifest:
        save_manifest(project, manifest)
    return code or 1


def do_package(project: Path, simulation_only: bool = False, *, version: str | None = None) -> int:
    manifest = load_manifest(project)
    sim_file = project / "reports" / "simulation.json"
    td_file = project / "reports" / "td.json"
    implement_file = project / "reports" / "implement.json"
    sim_result=json.loads(sim_file.read_text(encoding='utf-8')) if sim_file.is_file() else {}
    valid,reason=simulation_valid(project,manifest,sim_result)
    if not valid:
        print(f'Package gate: {reason}',file=sys.stderr)
        return 2
    # A vendor-neutral (non-Anlogic) target delivers through the open
    # implementation layer; the Anlogic TD flow remains the default path.
    from .targets import resolve_target
    from .implement import implement_valid as neutral_valid
    try:
        profile = resolve_target(manifest)
    except ValueError:
        # A manifest whose target cannot be resolved as a profile keeps the
        # legacy Anlogic TD delivery path rather than aborting packaging.
        profile = None
    vendor_neutral = bool(profile) and profile.get("backend") != "anlogic" and profile.get("capabilities", {}).get("bitstream")
    if not simulation_only and not vendor_neutral:
        td_result = json.loads(td_file.read_text(encoding='utf-8')) if td_file.is_file() else {}
        valid,reason=implementation_valid(project,manifest,td_result)
        if not valid:
            print(f'Package gate: {reason}',file=sys.stderr)
            return 2
    elif not simulation_only:
        implement_result = json.loads(implement_file.read_text(encoding='utf-8')) if implement_file.is_file() else {}
        valid,reason=neutral_valid(project,manifest,implement_result)
        if not valid:
            print(f'Package gate: {reason}',file=sys.stderr)
            return 2
    td_result = json.loads(td_file.read_text(encoding='utf-8')) if td_file.is_file() and not simulation_only else {}
    selected_run = project / td_result["run_directory"] if td_result.get("run_directory") else None
    from .quality import review_project
    review = review_project(project,'simulation' if simulation_only else 'fpga')
    if not review['artifact_ready']:
        raise ValueError('Engineering review rejected stale or incomplete artifact evidence.')
    verified_inputs = simulation_input_hashes(project,manifest)
    build_inputs = input_hashes(project,manifest)
    report_hashes = {name:sha(project/name) for name in ('reports/simulation.json','reports/td.json') if (project/name).is_file()}
    hardware_required=not simulation_only and manifest.get('hardware_acceptance',{}).get('required') is True
    if hardware_required:
        for name in ('reports/hardware-video.json','reports/programming.json'):
            report_hashes[name]=sha(project/name)
    target = project / "deliverables"
    target.mkdir(exist_ok=True)
    version_suffix = f"-{version}" if version else ""
    artifact = target / f"{manifest['project']}-{'simulation' if simulation_only else 'delivery'}{version_suffix}.zip"
    pending = target / f'.{artifact.name}.{uuid.uuid4().hex}.pending'

    def portable_text(path: Path) -> str:
        text = path.read_text(encoding="utf-8", errors="replace")
        project_root = project.resolve()
        for root in (str(project_root), project_root.as_posix()):
            text = text.replace(root, "$PROJECT_ROOT")
        for identity, replacement in ((getpass.getuser(), "$USER"), (platform.node(), "$HOST")):
            if identity:
                text = re.sub(re.escape(identity), replacement, text, flags=re.IGNORECASE)
        if path.suffix.lower() == ".al":
            text = re.sub(r'(?<=Path=")[^"]+(?=")', ".", text, count=1)
        return text

    with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for folder in ("rtl", "tb", "constraints", "board", "spec", "reports", "firmware", "model"):
            root = project / folder
            if root.exists():
                for path in root.rglob("*"):
                    if path.is_file() and path.name != "simulation.vvp" and path.suffix.lower() != ".log":
                        relative = path.relative_to(project)
                        # These reports are fingerprinted as literal bytes by
                        # the physical acceptance chain. Reformatting JSON (or
                        # normalizing Windows CRLF) invalidates that evidence.
                        bound_report=relative.as_posix() in {'reports/hardware-video.json','reports/programming.json'}
                        if path.suffix.lower() == '.json' and folder == 'reports' and not bound_report:
                            archive.writestr(relative.as_posix(), json.dumps(json.loads(portable_text(path)), ensure_ascii=False, indent=2) + "\n")
                        else:
                            archive.write(path, relative.as_posix())
        for name in ("README.md",):
            path = project / "td" / name
            if path.is_file():
                archive.write(path, path.relative_to(project).as_posix())
        archive.write(project / "project.json", "project.json")
        neutral_bitstream = None
        if vendor_neutral and not simulation_only:
            impl = implement_result.get("bitstream") or {}
            if impl.get("path"):
                neutral_bitstream = {"path": impl["path"], "sha256": impl.get("sha256"),
                                     "extension": impl.get("extension"),
                                     "target": implement_result.get("target", {}).get("id"),
                                     "backend": implement_result.get("backend")}
        delivery_bitstreams = (neutral_bitstream or {}).get("sha256") and {neutral_bitstream["path"]: neutral_bitstream["sha256"]} or td_result.get('bitstream_hashes', {})
        archive.writestr('delivery.json',json.dumps({'kind':'simulation-only' if simulation_only else 'implemented', 'inputs':build_inputs,'bitstreams':delivery_bitstreams,
            'vendor_neutral_bitstream':neutral_bitstream,
            'engineering_signoff':review['engineering_signoff'],'required_open_items':review['required_open_items'],
            'release_version': version,
            'hardware_report_sha256':report_hashes.get('reports/hardware-video.json')},indent=2))
        if hardware_required:
            programming=json.loads((project/'reports/programming.json').read_text(encoding='utf-8'))
            svf=(project/programming['svf_file']).resolve()
            if project.resolve() not in svf.parents:raise ValueError('Programmed SVF escapes project')
            archive.write(svf,svf.relative_to(project).as_posix())
        if selected_run and selected_run.is_dir():
            for path in selected_run.rglob("*.al"):
                if path.is_file():
                    archive.writestr(path.relative_to(project).as_posix(), portable_text(path))
            for path in selected_run.rglob("*"):
                if path.is_file() and "sources" in path.relative_to(selected_run).parts:
                    archive.write(path, path.relative_to(project).as_posix())
            for relative in td_result.get("bitstreams", []):
                bitstream = project / relative
                if bitstream.is_file():
                    archive.write(bitstream, bitstream.relative_to(project).as_posix())
            for pattern in ("*.rpt", "*.timing", "*.area", "flow.status"):
                for path in selected_run.rglob(pattern):
                    if path.is_file():
                        archive.writestr(path.relative_to(project).as_posix(), portable_text(path))
        if neutral_bitstream and neutral_bitstream.get("path"):
            bitstream_path = (project / neutral_bitstream["path"]).resolve()
            if project.resolve() not in bitstream_path.parents:
                raise ValueError('Vendor-neutral bitstream escapes the project.')
            archive.write(bitstream_path, bitstream_path.relative_to(project).as_posix())
        for name in hdl_input_hashes(project,manifest['rtl_sources']+manifest['testbench_sources'],allow_dynamic_memory=manifest.get('verification',{}).get('kind')=='riscv-retire'):
            if name not in archive.namelist():
                archive.write(project/name,name)
        # A declared checker/specification is part of delivery, even outside the conventional folders.
        for name in [key for key in verified_inputs if not key.startswith('@')]+['README.md']:
            if name not in archive.namelist() and (project/name).is_file():
                archive.write(project/name,name)
    # Read only after closing the writer: Python 3.11 on Windows can normalize
    # ZipInfo paths differently while reading entries from an open write handle.
    with zipfile.ZipFile(pending,'r') as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Delivery archive contains duplicate paths.')
        inventory = '\n'.join(f'{hashlib.sha256(archive.read(name)).hexdigest()}  {name}' for name in sorted(names))+'\n'
    with zipfile.ZipFile(pending,'a',compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('MANIFEST.sha256',inventory)
    try:
        if simulation_input_hashes(project,load_manifest(project)) != verified_inputs or input_hashes(project,load_manifest(project)) != build_inputs:
            raise ValueError('Delivery inputs changed while packaging; archive rejected.')
        if any(sha(project/name) != expected for name,expected in report_hashes.items()):
            raise ValueError('Verification reports changed while packaging; archive rejected.')
        current_manifest = load_manifest(project)
        valid,reason=simulation_valid(project,current_manifest,sim_result)
        if not valid:
            raise ValueError('Delivery simulation evidence rejected: '+reason)
        if not simulation_only:
            if vendor_neutral:
                impl_result = json.loads(implement_file.read_text(encoding='utf-8')) if implement_file.is_file() else {}
                valid,reason=neutral_valid(project,current_manifest,impl_result)
                if not valid:
                    raise ValueError('Delivery implementation evidence rejected: '+reason)
            else:
                valid,reason=implementation_valid(project,current_manifest,td_result)
                if not valid:
                    raise ValueError('Delivery implementation evidence rejected: '+reason)
        if hardware_required:
            from .video_hardware import evidence_valid
            hardware=json.loads((project/'reports/hardware-video.json').read_text(encoding='utf-8'))
            valid,reason=evidence_valid(project,hardware)
            if not valid:raise ValueError('Delivery hardware evidence rejected: '+reason)
        with zipfile.ZipFile(pending) as archive:
            for name,expected in verified_inputs.items():
                if name.startswith('@'):
                    continue
                if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                    raise ValueError(f'Archived verification input differs: {name}')
            for name,expected in td_result.get('bitstream_hashes',{}).items():
                if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                    raise ValueError(f'Archived bitstream differs: {name}')
            if neutral_bitstream and neutral_bitstream.get('path'):
                if hashlib.sha256(archive.read(neutral_bitstream['path'])).hexdigest() != neutral_bitstream.get('sha256'):
                    raise ValueError(f"Archived vendor-neutral bitstream differs: {neutral_bitstream['path']}")
            if hardware_required:
                for name,expected in hardware['artifacts'].items():
                    if hashlib.sha256(archive.read(name)).hexdigest()!=expected:
                        raise ValueError(f'Archived hardware capture differs: {name}')
        os.replace(pending,artifact)
    finally:
        pending.unlink(missing_ok=True)
    artifact.with_suffix('.zip.sha256').write_text(f'{sha(artifact)}  {artifact.name}\n',encoding='utf-8')
    print(f"交付包已生成：{artifact}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="fpga-agent", description="HX1P35A Verilog 设计、仿真与安路 TD 实现 agent")
    from . import __version__
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)

    tui = commands.add_parser("tui", help="打开 IC Forge 交互式终端工作台")
    tui.add_argument("--workspace", default=".", help="工作区目录")
    tui.add_argument("--resume", "--continue", "-c", nargs="?", const="latest", help="恢复会话 ID；省略 ID 恢复最近会话")

    init = commands.add_parser("init", help="创建 HX1P35A 示例或新设计工程")
    init.add_argument("project", help="工程目录名")
    init.add_argument("--directory", default=".", help="工程父目录，默认当前目录")

    design = commands.add_parser("design", help="调用模型生成 RTL/testbench 并仿真，失败时有限轮修复")
    design.add_argument("project")
    design.add_argument("--brief")
    design.add_argument("--brief-file")
    design.add_argument("--repair-rounds", type=int, default=2)
    design.add_argument('--provider',choices=('exchange','codex','api','chat'),default='exchange')
    design.add_argument('--model',default='gpt-6-luna')

    verify = commands.add_parser("verify", help="运行自检 testbench 并保存报告")
    verify.add_argument("project")
    mutation = commands.add_parser("mutation-audit", help="在隔离副本中审计 testbench 的 RTL 变异检出能力")
    mutation.add_argument("project")
    mutation.add_argument("--max-mutants", type=int, default=128)
    mutation.add_argument("--timeout", type=int, default=30, help="每个变异的仿真超时秒数")
    mutation.add_argument("--total-timeout", type=int, default=1800, help="审计总时限秒数")
    mutation.add_argument("--seed", type=int, default=1, help="稳定的变异样本种子")
    lec = commands.add_parser("lec", help="用 Yosys 证明 RTL 与门级网表（或两份 HDL 设计）逻辑等价")
    lec.add_argument("project")
    lec.add_argument("--mode", choices=("synth", "designs"), help="synth：RTL 对自身网表；designs：参考对实现")
    lec.add_argument("--top", help="顶层模块名，默认取 manifest")
    lec.add_argument("--gold", nargs="+", help="参考 HDL 源文件（designs 模式）")
    lec.add_argument("--gate", nargs="+", help="待验证实现 HDL 源文件（designs 模式）")
    lec.add_argument("--timeout", type=int, default=300, help="Yosys 超时秒数")
    formal = commands.add_parser("formal", help="用 Yosys 有界（BMC）或时序归纳证明 RTL 断言属性")
    formal.add_argument("project")
    formal.add_argument("--top", help="顶层模块名，默认取 manifest")
    formal.add_argument("--mode", choices=("bmc", "prove"), help="bmc：有界模型检测；prove：时序归纳")
    formal.add_argument("--depth", type=int, help="BMC 深度（周期数），默认取 manifest")
    formal.add_argument("--no-init-zero", dest="init_zero", action="store_false", default=None,
                        help="不假设所有触发器上电归零（默认按 FPGA 约定假设归零）")
    formal.add_argument("--timeout", type=int, default=300, help="Yosys 超时秒数")
    synth = commands.add_parser("synth", help="用 Yosys 综合出厂商中立的门级网表与资源报告")
    synth.add_argument("project")
    synth.add_argument("--top", help="顶层模块名，默认取 manifest")
    synth.add_argument("--no-flatten", dest="flatten", action="store_false", default=None,
                       help="保留层次结构（默认展平为单一网表）")
    synth.add_argument("--timeout", type=int, default=300, help="Yosys 超时秒数")
    svsim = commands.add_parser("svsim", help="用 Verilator 运行最小 class 化（UVM 风格）验证环境")
    svsim.add_argument("project")
    svsim.add_argument("--top", help="环境顶层模块名，默认取 manifest 的 svsim.top")
    svsim.add_argument("--timeout", type=int, default=300, help="Verilator 构建与运行超时秒数")
    commands.add_parser("targets", help="列出内置实现目标 profile（厂商中立多后端）")
    implement = commands.add_parser("implement", help="用所选目标后端把 RTL 实现到 bitstream（iCE40/Anlogic/通用）")
    implement.add_argument("project")
    implement.add_argument("--target", help="目标 profile id，默认取 manifest 的 target 或默认 profile")
    implement.add_argument("--top", help="顶层模块名，默认取 manifest")
    implement.add_argument("--stage", choices=("synth", "full"), help="实现阶段，默认 full（通用目标只用 synth）")
    implement.add_argument("--timeout", type=int, help="后端超时秒数，默认按后端选择")
    implement.add_argument("--td-root", help="Anlogic TD 安装目录（anlogic 后端）")
    coverage = commands.add_parser("coverage", help="用 Verilator 测量 RTL 结构覆盖率（行/分支/翻转）")
    coverage.add_argument("project")
    coverage.add_argument("--top", help="RTL 顶层模块名，默认取 manifest 的 top_module")
    coverage.add_argument("--testbench-top", help="testbench 顶层模块名，默认取 manifest 的 testbench_top")
    coverage.add_argument("--timeout", type=int, default=300, help="Verilator 构建与运行超时秒数")
    coverage.add_argument("--functional-coverage", action="store_true", default=None,
                          help="同时收集并要求 `cover property` 功能覆盖点（COV-002）")
    cdc = commands.add_parser("cdc", help="静态分析 RTL 的时钟/复位域交叉候选（CDC/RDC）")
    cdc.add_argument("project")
    cdc.add_argument("--timeout", type=int, default=60, help="分析超时秒数")
    timing = commands.add_parser("timing", help="读取布线后时序签核并尝试 post-route 时序仿真")
    timing.add_argument("project")
    timing.add_argument("--timeout", type=int, default=600, help="post-route 仿真超时秒数")
    video = commands.add_parser('video-hardware',help='终端采集 HDMI，并用独立几何参考验证实板立方体旋转')
    video.add_argument('project')
    video.add_argument('--device',default='Hagibis')
    video.add_argument('--seconds',type=int,default=12)
    video.add_argument('--settle',type=int,default=5)
    video.add_argument('--program',action='store_true',help='先将已通过门禁的位流下载到 SRAM')

    lint = commands.add_parser('lint',help='严格 Verilator RTL lint；缺少工具时明确阻塞')
    lint.add_argument('project')
    review = commands.add_parser('review',help='生成规格、设计、验证、实现与交付工程审查')
    review.add_argument('project')
    review.add_argument('--scope',choices=('simulation','fpga'),default='fpga')
    review.add_argument('--strict',action='store_true',help='有未关闭的必要工程检查时返回非零')

    td = commands.add_parser("td", help="仿真通过后调用 Anlogic TD Tcl 流程")
    td.add_argument("project")
    td.add_argument("--stage", choices=("synth", "full"), default="full")
    td.add_argument("--td-root")

    package = commands.add_parser("package", help="生成带源码、验证报告和可用 bitstream 的交付 zip")
    package.add_argument("project")
    package.add_argument('--simulation-only',action='store_true')
    release = commands.add_parser("release", help="聚合全部工程门禁并生成版本化交付")
    release.add_argument("project")
    release.add_argument("--version", help="发布版本号，默认取 release.json 或 0.6.0")
    release.add_argument('--simulation-only', action='store_true')
    flow=commands.add_parser('flow',help='verify, TD full, and evidence-gated delivery')
    flow.add_argument('project')
    flow.add_argument('--hardware-video',action='store_true',help='TD 后下载到 SRAM、采集 HDMI 并验证旋转，再交付')
    flow.add_argument('--device',default='Hagibis')
    cpu=commands.add_parser('cpu-design',help='generate/repair RV32I through selected model backend and independent verifier')
    cpu.add_argument('project')
    cpu.add_argument('--architecture',choices=('single','multi','pipeline'),required=True)
    cpu.add_argument('--provider',choices=('exchange','codex','api','chat'),default='exchange')
    cpu.add_argument('--model',default='gpt-6-luna')
    cpu.add_argument('--response-file')
    cpu.add_argument('--repair-rounds',type=int,default=2)
    cpu.add_argument('--seeds',type=int,default=64)
    cpu.add_argument('--full',action='store_true')
    tiny=commands.add_parser('tinyllm',help='TinyStories260K 独立参考、Luna RTL、生产固件仿真与 FPGA 实现')
    tiny.add_argument('project')
    tiny.add_argument('--prepare',action='store_true',help='创建工作负载工程并核验模型/板卡资料')
    tiny.add_argument('--board-docs',type=Path)
    tiny.add_argument('--model-dir',type=Path)
    tiny.add_argument('--full',action='store_true',help='验证通过后运行 TD 并打包；不等同于实板通过')
    tiny.add_argument('--port',help='使用现有有效验证/下载证据做实板验收，如 COM5 或 ftdi://123456789B')
    tiny.add_argument('--upload-model',action='store_true',help='把模型写入独立用户 N25Q128 Flash（需要 --port）')
    tiny.add_argument('--prompt',default='Once upon a time')
    tiny.add_argument('--new-tokens',type=int,default=8)
    tiny.add_argument('--program',action='store_true',help='通过现有 AL-LINK-FT:5555 服务下载已验收的 SRAM 位流')
    suite=commands.add_parser('cpu-suite',help='independent RV32I regression across prepared CPU designs')
    suite.add_argument('--root',default='benchmarks')
    suite.add_argument('--full',action='store_true')
    prune=commands.add_parser('prune-runs',help='清理旧 TD run；默认仅预览，使用 --apply 执行')
    prune.add_argument('project')
    prune.add_argument('--keep',type=int,default=5,help='保留最近 N 次及报告引用的 run')
    prune.add_argument('--apply',action='store_true',help='执行删除；默认 dry-run')
    training=commands.add_parser('training',help='RSI stage 6: train a tiny model on the host (SGD) and tie its forward pass to the tiny_train_gpu RTL math')
    training.add_argument('project',nargs='?',default='.')
    training.add_argument('--n',type=int,default=4,help='square model/GEMM dimension')
    training.add_argument('--samples',type=int,default=8)
    training.add_argument('--steps',type=int,default=200)
    training.add_argument('--lr',type=float,default=0.5)
    training.add_argument('--seed',type=int,default=1)
    from .autonomy import configure_parser
    configure_parser(commands)
    from .knowledge import configure_parser as knowledge_parser
    knowledge_parser(commands)
    return root


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["tui"]
    elif argv[0] in ("--workspace", "--resume", "--continue", "-c"):
        argv.insert(0, "tui")
    args = parser().parse_args(argv)
    try:
        if args.command == 'knowledge':
            from .knowledge import dispatch
            return dispatch(args)
        if args.command == "task":
            from .autonomy import dispatch
            return dispatch(args)
        if args.command == "tui":
            try:
                from .tui import launch
            except ImportError as exc:
                print("TUI 依赖缺失，请运行 python -m pip install -e .：" + str(exc), file=sys.stderr)
                return 2
            return launch(args.workspace, args.resume)
        if args.command == "init":
            project = create_project(args.project, Path(args.directory) / args.project)
            print(f"已创建工程：{project}")
            return 0
        if args.command=='video-hardware':
            from .video_hardware import hardware_run
            report=hardware_run(Path(args.project),device=args.device,seconds=args.seconds,
                                settle=args.settle,program=args.program)
            print(json.dumps(report,ensure_ascii=False,indent=2))
            return 0 if report['status']=='pass' else 1
        if args.command=='cpu-design':
            from .cpu_agent import design_cpu
            if not 0<=args.repair_rounds<=5:raise ValueError('repair-rounds must be 0..5')
            project=Path(args.project).resolve()
            result=design_cpu(project,args.architecture,provider=args.provider,model=args.model,rounds=args.repair_rounds,seeds=args.seeds,response_file=Path(args.response_file) if args.response_file else None)
            if result['status']!='pass':return 1
            if args.full:
                code=do_td(project,'full',None)
                if code:return code
                return do_package(project)
            return 0
        if args.command=='tinyllm':
            from .tinyllm import prepare,verify_project as verify_tiny
            project=Path(args.project).resolve()
            if args.upload_model and not args.port:
                raise ValueError('--upload-model requires --port')
            if args.program:
                if args.prepare or args.full:
                    raise ValueError('--program uses existing verified artifacts; run --full first')
                from .programmer import program_sram
                print(json.dumps(program_sram(project),ensure_ascii=False,indent=2))
                if not args.port:
                    return 0
            if args.port:
                if args.prepare or args.full:
                    raise ValueError('--port uses existing verified artifacts; run --prepare/--full separately first')
                from .tinyllm_hardware import hardware_run
                report=hardware_run(project,args.port,upload=args.upload_model,
                                    prompt=args.prompt,new_tokens=args.new_tokens)
                print(json.dumps(report,ensure_ascii=False,indent=2))
                return 0 if report.get('status')=='pass' else 1
            if args.prepare:
                prepare(project,board_docs=args.board_docs,source=args.model_dir)
            report=verify_tiny(project)
            print(json.dumps({k:v for k,v in report.items() if k in ('status','elapsed_seconds','rtl')},ensure_ascii=False,indent=2))
            if args.full:
                code=do_td(project,'full',None)
                return code if code else do_package(project)
            return 0
        if args.command=='cpu-suite':
            from .cpu_bench import run_benchmark
            root=Path(args.root).resolve();results=[]
            projects=[p for p in sorted(root.glob('rv32_*')) if (p/'project.json').is_file()]
            if not projects:raise ValueError('No prepared rv32_* projects found')
            for project in projects:
                report=run_benchmark(project)
                item={'project':project.name,'simulation':report['status'],'cases':report['case_count'],'coverage':report['instruction_coverage']}
                item['retired_instructions']=sum(c.get('retired',0) for c in report['cases'])
                item['cycles']=sum(c.get('cycles',0) for c in report['cases'])
                item['seeds']=report['seeds'];item['board_soc']=report['board_soc']
                if report['status']=='pass' and args.full:
                    item['td_returncode']=do_td(project,'full',None)
                    item['package_returncode']=do_package(project) if item['td_returncode']==0 else None
                td_path=project/'reports/td.json'
                if td_path.is_file():
                    td_report=json.loads(td_path.read_text(encoding='utf-8'))
                    valid,reason=implementation_valid(project,load_manifest(project),td_report)
                    item['implementation']='pass' if valid else 'fail'
                    item['implementation_reason']=reason
                    item['timing_summary']=td_report.get('timing_summary')
                    item['resource_summary']=td_report.get('resource_summary')
                    item['bitstreams']=td_report.get('bitstream_hashes')
                results.append(item)
            (root/'suite-report.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
            return 0 if all(r['simulation']=='pass' and (not args.full or r.get('package_returncode')==0) for r in results) else 1
        if args.command=='prune-runs':
            from .retention import prune_td_runs
            result=prune_td_runs(resolve_project(args.project),keep=args.keep,apply=args.apply)
            print(json.dumps(result,ensure_ascii=False,indent=2))
            return 0
        if args.command=='training':
            from .training import run_training
            project=Path(args.project).resolve()
            report=run_training(project,n=args.n,samples=args.samples,steps=args.steps,lr=args.lr,seed=args.seed)
            print(json.dumps({key:report.get(key) for key in (
                'status','seed','n','samples','steps','learning_rate','initial_loss','final_loss',
                'loss_reduction_percent','forward_match_percent','forward_mean_magnitude_error',
                'weight_sha256','reason')},ensure_ascii=False,indent=2))
            return 0 if report['status']=='pass' else 1
        if args.command == 'targets':
            from .targets import BUILTIN_TARGETS
            print(json.dumps({'targets': list(BUILTIN_TARGETS.values())}, ensure_ascii=False, indent=2))
            return 0
        project = resolve_project(args.project)
        if args.command == 'lint':
            from .quality import lint_project
            report = lint_project(project)
            print(report['output'])
            print('Lint: '+report['status'])
            return 0 if report['status']=='pass' else 2 if report['status']=='blocked' else 1
        if args.command == 'review':
            from .quality import review_project
            report = review_project(project,args.scope)
            print((project/'reports/engineering-review.md').read_text(encoding='utf-8'))
            return 0 if report['artifact_ready'] and (not args.strict or report['required_open_items']==0) else 2
        if args.command == "design":
            if args.repair_rounds < 0 or args.repair_rounds > 5:
                raise ValueError("--repair-rounds 范围为 0 到 5。")
            return do_design(project, _read_brief(project, args.brief, args.brief_file), args.repair_rounds,args.provider,args.model)
        if args.command == "verify":
            result = verify_project(project)
            print(result.output, end="")
            print(f"\n仿真状态：{result.status}（{result.elapsed_seconds:.3f}s）")
            return 0 if result.status == "pass" else 1
        if args.command == "mutation-audit":
            from .mutation import mutation_audit
            progress = lambda message: print(message, flush=True)
            report = mutation_audit(project, max_mutants=args.max_mutants, timeout=args.timeout,
                                    total_timeout=args.total_timeout, seed=args.seed, progress=progress)
            print(json.dumps({key: report.get(key) for key in (
                "project", "status", "baseline", "candidate_count", "selected_count", "completed_count",
                "candidate_selection_truncated", "seed", "counts", "observed_kill_rate_percent", "elapsed_seconds",
                "reason", "note") if key in report}, ensure_ascii=False, indent=2))
            print(f"完整变异清单：{project / 'reports' / 'mutation.json'}")
            return 0 if report.get("status") == "complete" else 2
        if args.command == 'lec':
            from .formal import run_lec
            progress = lambda message: print(message, flush=True)
            report = run_lec(project, mode=args.mode, top=args.top, gold=args.gold,
                             gate=args.gate, timeout=args.timeout, progress=progress)
            print(json.dumps({key: report.get(key) for key in (
                "project", "status", "mode", "top", "proven", "unproven", "unproven_points",
                "netlist", "tool_version", "elapsed_seconds", "reason") if key in report},
                ensure_ascii=False, indent=2))
            print(f"完整 LEC 报告：{project / 'reports' / 'lec.json'}")
            return 0 if report.get("status") == "pass" else 1 if report.get("status") == "fail" else 2
        if args.command == 'formal':
            from .formal import run_formal
            progress = lambda message: print(message, flush=True)
            report = run_formal(project, top=args.top, depth=args.depth, mode=args.mode,
                                set_init_zero=args.init_zero, timeout=args.timeout, progress=progress)
            print(json.dumps({key: report.get(key) for key in (
                "project", "status", "mode", "bounded", "depth", "steps", "set_init_zero",
                "assertions", "top", "tool_version", "elapsed_seconds", "reason") if key in report},
                ensure_ascii=False, indent=2))
            print(f"完整形式化报告：{project / 'reports' / 'formal.json'}")
            return 0 if report.get("status") == "pass" else 1 if report.get("status") == "fail" else 2
        if args.command == 'synth':
            from .synth import run_synth
            progress = lambda message: print(message, flush=True)
            report = run_synth(project, top=args.top, flatten=args.flatten,
                               timeout=args.timeout, progress=progress)
            print(json.dumps({key: report.get(key) for key in (
                "project", "status", "top", "flatten", "cells", "flops", "netlist",
                "tool_version", "elapsed_seconds", "reason") if key in report},
                ensure_ascii=False, indent=2))
            print(f"完整综合报告：{project / 'reports' / 'synth.json'}")
            return 0 if report.get("status") == "pass" else 2
        if args.command == 'svsim':
            from .svsim import run_svsim
            progress = lambda message: print(message, flush=True)
            report = run_svsim(project, top=args.top, timeout=args.timeout, progress=progress)
            print(json.dumps({key: report.get(key) for key in (
                "project", "status", "top", "tool_version", "cxx", "make", "checks",
                "elapsed_seconds", "reason") if key in report}, ensure_ascii=False, indent=2))
            print(f"完整 class 环境报告：{project / 'reports' / 'svsim.json'}")
            return 0 if report.get("status") == "pass" else 2
        if args.command == 'implement':
            from .implement import run_implement
            progress = lambda message: print(message, flush=True)
            report = run_implement(project, target=args.target, top=args.top, stage=args.stage,
                                   timeout=args.timeout, td_root=args.td_root, progress=progress)
            print(json.dumps({key: report.get(key) for key in (
                "project", "status", "backend", "stage", "target", "capabilities", "top",
                "stages", "utilization", "timing", "bitstream", "elapsed_seconds", "reason")
                if key in report}, ensure_ascii=False, indent=2))
            print(f"完整实现报告：{project / 'reports' / 'implement.json'}")
            return 0 if report.get("status") == "pass" else 2
        if args.command == 'coverage':
            from .coverage import run_coverage
            progress = lambda message: print(message, flush=True)
            report = run_coverage(project, top=args.top, testbench_top=args.testbench_top,
                                  timeout=args.timeout, progress=progress,
                                  functional_coverage=args.functional_coverage)
            summary = report.get("coverage") or {}
            functional = report.get("functional") or {}
            print(json.dumps({key: report.get(key) for key in (
                "project", "status", "structural_status", "functional_status",
                "top", "testbench_top", "tool_version", "cxx",
                "returncode", "elapsed_seconds", "assertion_failure", "reason")
                if key in report}, ensure_ascii=False, indent=2))
            print(json.dumps({
                "structural_coverage_percent": summary.get("coverage_percent"),
                "structural_points": summary.get("total_points"),
                "functional_coverage_percent": functional.get("coverage_percent"),
                "functional_points": functional.get("total_points"),
            }, ensure_ascii=False))
            print(f"完整覆盖率报告：{project / 'reports' / 'coverage.json'}")
            return 0 if report.get("status") == "pass" else 2
        if args.command == 'cdc':
            from .cdc import run_cdc
            report = run_cdc(project, timeout=args.timeout)
            print(json.dumps({key: report.get(key) for key in (
                "project", "status", "clocks", "crossings", "reset_domains", "reason")
                if key in report}, ensure_ascii=False, indent=2))
            print(f"完整 CDC 报告：{project / 'reports' / 'cdc.json'}")
            return 0 if report.get("status") == "pass" else 2
        if args.command == "timing":
            from .timing import run_timing
            report = run_timing(project, timeout=args.timeout)
            print(json.dumps({key: report.get(key) for key in (
                "project", "status", "timing_status", "post_route_status", "timing", "reason")
                if key in report}, ensure_ascii=False, indent=2))
            print(f"完整时序报告：{project / 'reports' / 'timing.json'}")
            return 0 if report.get("status") == "pass" else 2
        if args.command == "td":
            return do_td(project, args.stage, args.td_root)
        if args.command == "package":
            return do_package(project,args.simulation_only)
        if args.command == "release":
            from .release import run_release
            release = run_release(project, version=args.version)
            print(json.dumps({"project": release.get("project"),
                              "version": release.get("version"),
                              "aggregate": release.get("aggregate"),
                              "reason": release.get("reason")}, ensure_ascii=False, indent=2))
            code = do_package(project, args.simulation_only, version=release.get("version"))
            return code
        if args.command=='flow':
            result=verify_project(project)
            if result.status!='pass':return 1
            code=do_td(project,'full',None)
            if code:return code
            if args.hardware_video:
                from .video_hardware import hardware_run
                report=hardware_run(project,device=args.device,program=True)
                if report['status']!='pass':return 1
            return do_package(project)
        raise ValueError(f"不支持的命令：{args.command}")
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
