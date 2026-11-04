from __future__ import annotations

import os
import re
import shutil
import subprocess
import math
from datetime import datetime
from pathlib import Path

from .project import load_manifest
from .evidence import hdl_input_hashes


def parse_timing_summary(text: str) -> dict:
    number = r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|[+-]?(?:nan|inf(?:inity)?))'
    result = {}
    for marker, key in (('SWNS','setup_wns_ns'),('STNS','setup_tns_ns'),('HWNS','hold_wns_ns'),('HTNS','hold_tns_ns')):
        match = re.search(rf'\b{marker}:\s*{number}\s*ns', text, re.IGNORECASE)
        result[key] = float(match.group(1)) if match else None
    return result


DEFAULT_TD_ROOT = Path(r"C:\Anlogic\TD_6.2.1_Engineer_6.2.168.116")


def find_td_command(td_root: str | None = None) -> Path:
    if os.name != "nt":
        raise FileNotFoundError("Anlogic TD 命令行仅在 Windows 上受支持。")
    root_value = td_root or os.environ.get("ANLOGIC_TD_ROOT")
    root = Path(root_value) if root_value else DEFAULT_TD_ROOT
    candidate = root / "bin" / "td_commands_prompt.exe"
    if candidate.is_file():
        return candidate
    on_path = shutil.which("td_commands_prompt.exe")
    if on_path:
        return Path(on_path)
    raise FileNotFoundError(
        "没有找到 Anlogic TD 命令行 td_commands_prompt.exe。请设置 ANLOGIC_TD_ROOT 指向 TD 安装目录。"
    )


def _tcl_braced(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise ValueError("Tcl 参数不能包含换行符。")
    # A quoted Tcl word preserves path backslashes and treats braces as literal
    # characters. Escape substitutions that remain active inside double quotes.
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("[", "\\[")
    )
    return '"' + escaped + '"'


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def build_tcl(project: Path, stage: str) -> tuple[Path, Path]:
    manifest = load_manifest(project)
    if not re.fullmatch(r"[A-Za-z0-9_]+", manifest.get("device_family", "")) or not re.fullmatch(
        r"[A-Za-z0-9_]+", manifest.get("device", "")
    ):
        raise ValueError("device_family/device 必须是无脚本字符的 TD 标识符。")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,48}", manifest.get("project", "")):
        raise ValueError("工程名不符合 TD 工程命名规则。")
    if stage not in {"synth", "full"}:
        raise ValueError("TD 阶段只支持 synth 或 full。")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    project_name = f"{manifest['project']}_{stage}"
    run_dir = project / "td" / "runs" / f"run_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    copied_inputs: list[tuple[str, Path]] = []
    dependencies = hdl_input_hashes(project,manifest['rtl_sources'])
    input_names = list(dict.fromkeys(manifest['rtl_sources'] + list(dependencies))) + [manifest["board_constraints"], manifest["timing_constraints"]]
    for name in input_names:
        normalized_name = name.replace("\\", "/") if isinstance(name, str) else ""
        if not normalized_name or any(
            not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part) for part in Path(normalized_name).parts
        ):
            raise ValueError(f"TD 输入文件名含不支持的字符：{name}")
        source = (project / name).resolve()
        if project.resolve() not in source.parents or not source.is_file():
            raise ValueError(f"TD 输入文件越出工程目录或不存在：{name}")
        destination = run_dir / "sources" / Path(name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied_inputs.append((Path("sources") / Path(name).as_posix(), destination))
    lines = [
        f"set project_name {_tcl_braced(project_name)}",
        f"set project_dir {_tcl_braced(run_dir.as_posix())}",
        "cd $project_dir",
        f"create_project $project_name . -family {manifest['device_family']} -device {manifest['device']}",
        f"top_module {_tcl_braced(manifest['top_module'])}",
    ]
    for relative, _ in copied_inputs[: len(manifest["rtl_sources"])]:
        lines.append(f"add_files {_tcl_braced(relative.as_posix())}")
    adc_relative, sdc_relative = copied_inputs[-2][0], copied_inputs[-1][0]
    lines.extend(
        [
            f"add_files {_tcl_braced(adc_relative.as_posix())}",
            f"add_files {_tcl_braced(sdc_relative.as_posix())}",
        "launch_runs syn_1",
        "wait_run syn_1 -quiet",
        ]
    )
    if stage == "full":
        lines.extend(
            [
                "launch_runs phy_1",
                "wait_run phy_1 -quiet",
            ]
        )
    lines.extend(["close_project", "puts {FPGA_AGENT_TD_FLOW_DONE}"])
    path = project / "td" / "generated_flow.tcl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, run_dir


def run_td(
    project: Path, stage: str, td_root: str | None = None, timeout: int = 2400
) -> tuple[int, str, list[str], Path]:
    executable = find_td_command(td_root)
    flow, run_dir = build_tcl(project, stage)
    # The official Windows command prompt accepts TCL from stdin. If this launcher
    # does not inherit redirected input on a particular TD build, surface the exact
    # source command in the error log for a manual replay.
    command = [str(executable)]
    tcl_source = f"source {_tcl_braced(flow.as_posix())}"
    try:
        proc = subprocess.run(
            command,
            input=f"{tcl_source}\nexit\n",
            cwd=run_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = _as_text(exc.stdout) + _as_text(exc.stderr) + f"\nTD flow 超时（>{timeout}s）。\n"
        return 124, output, command, run_dir
    output = _as_text(proc.stdout) + _as_text(proc.stderr)
    if "FPGA_AGENT_TD_FLOW_DONE" not in output and proc.returncode == 0:
        output += f"\nTD 没有确认流程完成；可在 TD CMD 中运行：{tcl_source}\n"
        return 1, output, command, run_dir
    if proc.returncode != 0:
        output += f"\nTD 工作目录：{run_dir}\n可在 TD CMD 中重放：{tcl_source}\n"
    project_name = f"{load_manifest(project)['project']}_{stage}"
    runs = run_dir / f"{project_name}_Runs"
    required = ["Run syn_1 success"]
    if stage == "full":
        required.append("Run phy_1 success")
    missing = [marker for marker in required if marker not in output]
    bitstreams = [path for path in runs.glob('phy_1/*.bit') if path.is_file() and path.stat().st_size > 0] if stage == 'full' else []
    if proc.returncode == 0 and (missing or (stage == "full" and not bitstreams)):
        output += f"\nTD 返回码为 0，但未找到完整的预期成功标记：{missing}\n"
        if stage == "full" and not bitstreams:
            output += "TD 未生成任何 .bit bitstream 文件。\n"
        return 1, output, command, run_dir
    if proc.returncode == 0 and stage == "full":
        timing_report = runs / "phy_1" / "final_timing.rpt"
        report_text = timing_report.read_text(encoding="utf-8", errors="replace") if timing_report.is_file() else ""
        timing = parse_timing_summary(report_text)
        if any(value is None for value in timing.values()):
            output += "\nTD 没有生成可解析的最终 setup/hold 时序报告，full gate 未通过。\n"
            return 1, output, command, run_dir
        if not all(math.isfinite(value) for value in timing.values()):
            output += f"\n时序报告包含非有限 WNS/TNS：{timing}。\n"
            return 1, output, command, run_dir
        if timing['setup_wns_ns'] < 0 or timing['hold_wns_ns'] < 0 or timing['setup_tns_ns'] != 0 or timing['hold_tns_ns'] != 0:
            output += f"\n时序未收敛：{timing}。\n"
            return 1, output, command, run_dir
    return proc.returncode, output, command, run_dir
