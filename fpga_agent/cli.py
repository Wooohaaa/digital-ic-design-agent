from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import platform
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .model import generate_payload,ModelOutputError
from .project import create_project, load_manifest, resolve_project, save_manifest, write_model_artifacts
from .td import run_td
from .verify import verify_project
from .evidence import input_hashes, simulation_valid, implementation_valid, sha


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
    diagnostics = ""
    for attempt in range(repair_rounds + 1):
        try:
            payload = generate_payload(
                brief,
                _board_profile(project),
                current_rtl=_hdl_text(project, manifest, "rtl_sources", 60_000) if attempt else "",
                current_tb=_hdl_text(project, manifest, "testbench_sources", 40_000) if attempt else "",
                diagnostics=diagnostics,
                provider=provider,model=model,workspace=project,audit_dir=project/'generation'/f'round_{attempt:02}',
            )
            write_model_artifacts(project, payload)
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


def do_td(project: Path, stage: str, td_root: str | None) -> int:
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
    for name in manifest['rtl_sources']+[manifest['board_constraints'],manifest['timing_constraints']]:
        snapshot=run_dir/'sources'/name
        if not snapshot.is_file() or sha(snapshot)!=build_inputs.get(name):
            code=1;output+=f'\nTD source snapshot mismatch: {name}\n'
    project_name = f"{manifest['project']}_{stage}"
    runs_dir = run_dir / f"{project_name}_Runs"
    bitstreams = [p.relative_to(project).as_posix() for p in runs_dir.glob("phy_1/*.bit")]
    timing_report = runs_dir / "phy_1" / "final_timing.rpt"
    timing = None
    resources = None
    if timing_report.is_file():
        import re

        timing_text = timing_report.read_text(encoding="utf-8", errors="replace")
        setup = re.search(r"\bSWNS:\s*(-?[0-9.]+)ns,\s*STNS:\s*(-?[0-9.]+)ns", timing_text)
        hold = re.search(r"\bHWNS:\s*(-?[0-9.]+)ns,\s*HTNS:\s*(-?[0-9.]+)ns", timing_text)
        timing = {
            "setup_wns_ns": float(setup.group(1)) if setup else None,
            "setup_tns_ns": float(setup.group(2)) if setup else None,
            "hold_wns_ns": float(hold.group(1)) if hold else None,
            "hold_tns_ns": float(hold.group(2)) if hold else None,
        }
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
        save_manifest(project, manifest)
        return 0
    manifest["td_status"] = "fail"
    save_manifest(project, manifest)
    return code or 1


def do_package(project: Path, simulation_only: bool = False) -> int:
    manifest = load_manifest(project)
    sim_file = project / "reports" / "simulation.json"
    td_file = project / "reports" / "td.json"
    sim_result=json.loads(sim_file.read_text(encoding='utf-8')) if sim_file.is_file() else {}
    valid,reason=simulation_valid(project,manifest,sim_result)
    if not valid:
        print(f'Package gate: {reason}',file=sys.stderr)
        return 2
    td_result = json.loads(td_file.read_text(encoding='utf-8')) if td_file.is_file() and not simulation_only else {}
    if not simulation_only:
        valid,reason=implementation_valid(project,manifest,td_result)
        if not valid:
            print(f'Package gate: {reason}',file=sys.stderr)
            return 2
    selected_run = project / td_result["run_directory"] if td_result.get("run_directory") else None
    target = project / "deliverables"
    target.mkdir(exist_ok=True)
    artifact = target / f"{manifest['project']}-{'simulation' if simulation_only else 'delivery'}.zip"

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

    with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for folder in ("rtl", "tb", "constraints", "board", "spec", "reports"):
            root = project / folder
            if root.exists():
                for path in root.rglob("*"):
                    if path.is_file() and path.name != "simulation.vvp" and path.suffix.lower() != ".log":
                        relative = path.relative_to(project)
                        if path.suffix.lower() == ".json":
                            archive.writestr(relative.as_posix(), json.dumps(json.loads(portable_text(path)), ensure_ascii=False, indent=2) + "\n")
                        else:
                            archive.write(path, relative)
        for name in ("README.md",):
            path = project / "td" / name
            if path.is_file():
                archive.write(path, path.relative_to(project))
        archive.write(project / "project.json", "project.json")
        archive.writestr('delivery.json',json.dumps({'kind':'simulation-only' if simulation_only else 'implemented', 'inputs':input_hashes(project,manifest),'bitstreams':td_result.get('bitstream_hashes',{})},indent=2))
        if selected_run and selected_run.is_dir():
            for path in selected_run.rglob("*.al"):
                if path.is_file():
                    archive.writestr(path.relative_to(project).as_posix(), portable_text(path))
            for path in selected_run.rglob("*"):
                if path.is_file() and "sources" in path.relative_to(selected_run).parts and path.suffix.lower() in {".v", ".sv", ".adc", ".sdc"}:
                    archive.write(path, path.relative_to(project))
            for relative in td_result.get("bitstreams", []):
                bitstream = project / relative
                if bitstream.is_file():
                    archive.write(bitstream, bitstream.relative_to(project))
            for pattern in ("*.rpt", "*.timing", "*.area", "flow.status"):
                for path in selected_run.rglob(pattern):
                    if path.is_file():
                        archive.writestr(path.relative_to(project).as_posix(), portable_text(path))
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

    td = commands.add_parser("td", help="仿真通过后调用 Anlogic TD Tcl 流程")
    td.add_argument("project")
    td.add_argument("--stage", choices=("synth", "full"), default="full")
    td.add_argument("--td-root")

    package = commands.add_parser("package", help="生成带源码、验证报告和可用 bitstream 的交付 zip")
    package.add_argument("project")
    package.add_argument('--simulation-only',action='store_true')
    flow=commands.add_parser('flow',help='verify, TD full, and evidence-gated delivery')
    flow.add_argument('project')
    cpu=commands.add_parser('cpu-design',help='generate/repair RV32I through selected model backend and independent verifier')
    cpu.add_argument('project')
    cpu.add_argument('--architecture',choices=('single','multi','pipeline'),required=True)
    cpu.add_argument('--provider',choices=('exchange','codex','api','chat'),default='exchange')
    cpu.add_argument('--model',default='gpt-6-luna')
    cpu.add_argument('--response-file')
    cpu.add_argument('--repair-rounds',type=int,default=2)
    cpu.add_argument('--seeds',type=int,default=64)
    cpu.add_argument('--full',action='store_true')
    suite=commands.add_parser('cpu-suite',help='independent RV32I regression across prepared CPU designs')
    suite.add_argument('--root',default='benchmarks')
    suite.add_argument('--full',action='store_true')
    return root


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["tui"]
    elif argv[0] in ("--workspace", "--resume", "--continue", "-c"):
        argv.insert(0, "tui")
    args = parser().parse_args(argv)
    try:
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
        project = resolve_project(args.project)
        if args.command == "design":
            if args.repair_rounds < 0 or args.repair_rounds > 5:
                raise ValueError("--repair-rounds 范围为 0 到 5。")
            return do_design(project, _read_brief(project, args.brief, args.brief_file), args.repair_rounds,args.provider,args.model)
        if args.command == "verify":
            result = verify_project(project)
            print(result.output, end="")
            print(f"\n仿真状态：{result.status}（{result.elapsed_seconds:.3f}s）")
            return 0 if result.status == "pass" else 1
        if args.command == "td":
            return do_td(project, args.stage, args.td_root)
        if args.command == "package":
            return do_package(project,args.simulation_only)
        if args.command=='flow':
            result=verify_project(project)
            if result.status!='pass':return 1
            code=do_td(project,'full',None)
            return code if code else do_package(project)
        raise ValueError(f"不支持的命令：{args.command}")
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
