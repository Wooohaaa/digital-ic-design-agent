from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from . import __version__


def template_dir() -> Path:
    return Path(__file__).resolve().parent / "templates" / "hx1p35a_heartbeat"


def safe_project_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,47}", value):
        raise ValueError("工程名需以英文字母开头，只能包含英文字母、数字、下划线和短横线，长度不超过 48。")
    return value


def create_project(name: str, destination: Path) -> Path:
    safe_project_name(name)
    target = destination.resolve()
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"目标目录非空：{target}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_dir(), target, dirs_exist_ok=True)
    manifest = load_manifest(target)
    manifest["project"] = name
    manifest["agent_version"] = __version__
    save_manifest(target, manifest)
    return target


def load_manifest(project: Path) -> dict[str, Any]:
    path = project / "project.json"
    if not path.is_file():
        raise FileNotFoundError(f"找不到 {path}，先运行 `fpga-agent init`。")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("top_module"), str):
        raise ValueError(f"工程清单无效：{path}")
    return data


def save_manifest(project: Path, manifest: dict[str, Any]) -> None:
    (project / "project.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _ansi_top_ports(rtl: str, top: str) -> tuple[dict[str, str], set[str]]:
    source = re.sub(r"/\*.*?\*/|//[^\r\n]*", " ", rtl, flags=re.S)
    module = re.search(rf"\bmodule\s+{re.escape(top)}\b", source)
    if not module:
        raise ValueError(f"无法解析顶层模块 {top} 的 ANSI 端口声明；请使用标准 ANSI-style module header。")

    def closing_paren(text: str, opening: int) -> int:
        depth = 0
        for index in range(opening, len(text)):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
                if depth == 0:
                    return index
        raise ValueError(f"顶层模块 {top} 的端口声明括号不匹配。")

    cursor = module.end()
    while cursor < len(source) and source[cursor].isspace():
        cursor += 1
    if cursor < len(source) and source[cursor] == "#":
        cursor += 1
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
        if cursor >= len(source) or source[cursor] != "(":
            raise ValueError(f"顶层模块 {top} 的参数声明格式无效。")
        cursor = closing_paren(source, cursor) + 1
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
    if cursor >= len(source) or source[cursor] != "(":
        raise ValueError(f"顶层模块 {top} 必须使用 ANSI-style 端口声明。")
    end = closing_paren(source, cursor)
    if not re.match(r"\s*;", source[end + 1 :]):
        raise ValueError(f"顶层模块 {top} 的 ANSI 端口声明后缺少分号。")

    header = source[cursor + 1 : end]
    if not header.strip():
        return {}, set()
    segments: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(header):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                raise ValueError(f"顶层模块 {top} 的端口声明括号不匹配。")
        elif char == "," and depth == 0:
            segments.append(header[start:index].strip())
            start = index + 1
    if depth != 0:
        raise ValueError(f"顶层模块 {top} 的端口声明括号不匹配。")
    segments.append(header[start:].strip())

    ports: dict[str, str] = {}
    vector_ports: set[str] = set()
    current_direction: str | None = None
    inherited_packed_dims = ""
    declaration_re = re.compile(
        r"^(?:(wire|reg|logic|tri|tri0|tri1|wand|wor|uwire|bit|var)\s+)?"
        r"(?:(signed|unsigned)\s+)?((?:\[[^\]]+\]\s*)*)"
        r"([A-Za-z_][A-Za-z0-9_$]*)$"
    )
    for segment in segments:
        if not segment:
            raise ValueError(f"顶层模块 {top} 包含空端口声明。")
        direction_match = re.match(r"^(input|output|inout)\b\s*", segment)
        if direction_match:
            current_direction = direction_match.group(1)
            segment = segment[direction_match.end() :].strip()
            declaration = declaration_re.fullmatch(segment)
            if not declaration:
                raise ValueError(f"顶层端口声明格式不支持或含糊：{segment}")
            inherited_packed_dims = declaration.group(3)
            port = declaration.group(4)
        else:
            if current_direction is None:
                raise ValueError(f"顶层端口声明缺少 input/output/inout 方向，且无法从前一端口继承：{segment}")
            # In ANSI headers, a comma-separated name inherits the previous
            # direction, type, signedness, and packed dimensions.
            continuation = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_$]*)", segment)
            if not continuation:
                raise ValueError(f"顶层端口声明格式不支持或含糊：{segment}")
            port = continuation.group(1)
        if current_direction is None:
            raise ValueError(f"顶层端口声明缺少 input/output/inout 方向，且无法从前一端口继承：{segment}")
        if port in ports:
            raise ValueError(f"顶层模块 {top} 重复声明端口 {port}。")
        ports[port] = current_direction
        if inherited_packed_dims:
            vector_ports.add(port)
    if not ports:
        raise ValueError(f"顶层模块 {top} 没有端口声明。")
    return ports, vector_ports


def _render_adc(
    board_profile: dict[str, Any], ports: dict[str, str], vector_ports: set[str], bindings: dict[str, str]
) -> tuple[str, list[str]]:
    signals = board_profile.get("signals", {})
    lines: list[str] = []
    unmapped: list[str] = []
    bound_pins: dict[str, str] = {}
    for port, direction in ports.items():
        signal_name = bindings.get(port)
        if signal_name is None:
            unmapped.append(port)
            continue
        signal = signals.get(signal_name)
        if not isinstance(signal, dict):
            raise ValueError(f"board_io 引用了板卡资料中未知的信号：{signal_name}")
        if signal.get("direction") != direction:
            raise ValueError(f"端口 {port} 是 {direction}，不能绑定到板卡 {signal_name} ({signal.get('direction')})。")
        if port in vector_ports:
            raise ValueError(f"暂不支持将多位向量端口 {port} 映射到单个板级引脚。")
        pin = signal.get("pin")
        io_standard = signal.get("io_standard")
        if (
            not isinstance(pin, str)
            or not re.fullmatch(r"[A-Za-z][0-9]+", pin)
            or not isinstance(io_standard, str)
            or not re.fullmatch(r"[A-Za-z0-9_]+", io_standard)
        ):
            raise ValueError(f"板卡信号 {signal_name} 缺少已确认的 pin 或 IO standard。")
        pin_key = pin.upper()
        previous_port = bound_pins.get(pin_key)
        if previous_port is not None:
            raise ValueError(f"顶层端口 {previous_port} 和 {port} 不能同时绑定到板卡引脚 {pin}。")
        bound_pins[pin_key] = port
        options = [f"LOCATION = {pin}", f"IOSTANDARD = {io_standard}"]
        if "drive_strength" in signal:
            drive = signal["drive_strength"]
            if not isinstance(drive, int) or drive not in {2, 4, 6, 8, 12, 16}:
                raise ValueError(f"板卡信号 {signal_name} 的 drive_strength 无效。")
            options.append(f"DRIVESTRENGTH = {drive}")
        if "pull" in signal:
            pull = signal["pull"]
            if pull not in {"PULLUP", "PULLDOWN", "NONE"}:
                raise ValueError(f"板卡信号 {signal_name} 的 pull 类型无效。")
            options.append(f"PULLTYPE = {pull}")
        lines.append(f"set_pin_assignment {{ {port} }} {{ {'; '.join(options)}; }}")
    return "\n".join(lines) + ("\n" if lines else ""), unmapped


def resolve_project(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"工程不存在：{path}")
    load_manifest(path)
    return path


def _atomic_write(project: Path, writes: dict[str, str]) -> None:
    """Stage all files, then replace them as a rollback-capable transaction."""
    root = project.resolve()
    targets: dict[str, Path] = {}
    for relative in writes:
        target = project / relative
        resolved = target.resolve()
        if root not in resolved.parents:
            raise ValueError(f"文件路径越出工程目录：{relative}")
        cursor = project
        for part in Path(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError(f"拒绝通过符号链接写入工程文件：{relative}")
        if target.exists() and not target.is_file():
            raise ValueError(f"写入目标不是普通文件：{relative}")
        targets[relative] = target

    with tempfile.TemporaryDirectory(prefix=".fpga-agent-write-", dir=project.parent) as temp_name:
        temp_root = Path(temp_name)
        staged_root = temp_root / "staged"
        backup_root = temp_root / "backup"
        for relative, content in writes.items():
            staged = staged_root / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_text(content, encoding="utf-8", newline="\n")
            target = targets[relative]
            if target.is_file():
                backup = backup_root / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)

        created_dirs: list[Path] = []
        committed: list[str] = []
        try:
            for relative, target in targets.items():
                missing: list[Path] = []
                parent = target.parent
                while not parent.exists() and parent != project.parent:
                    missing.append(parent)
                    parent = parent.parent
                target.parent.mkdir(parents=True, exist_ok=True)
                created_dirs.extend(reversed(missing))
                os.replace(staged_root / relative, target)
                committed.append(relative)
        except Exception:
            for relative in reversed(committed):
                target = targets[relative]
                backup = backup_root / relative
                try:
                    if backup.is_file():
                        os.replace(backup, target)
                    elif target.exists():
                        target.unlink()
                except OSError:
                    # Preserve the original exception; rollback has been attempted for every file.
                    pass
            for directory in reversed(created_dirs):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise


def write_model_artifacts(project: Path, payload: dict[str, Any]) -> None:
    """Validate the complete payload before committing any project-file changes."""
    current_manifest=load_manifest(project)
    if current_manifest.get('verification',{}).get('kind')=='riscv-retire':
        raise ValueError('CPU verification harness is protected; use cpu-design to replace only the core RTL.')
    top = payload.get("top_module")
    identifier = r"[A-Za-z_][A-Za-z0-9_$]{0,127}"
    if not isinstance(top, str) or not re.fullmatch(identifier, top):
        raise ValueError("模型返回的 top_module 不是合法 Verilog 标识符。")
    testbench_top = payload.get("testbench_top")
    if not isinstance(testbench_top, str) or not re.fullmatch(identifier, testbench_top):
        raise ValueError("模型返回的 testbench_top 不是合法 Verilog 标识符。")

    files = payload.get("files")
    if not isinstance(files, dict) or len(files) < 2 or len(files) > 32:
        raise ValueError("模型必须返回至少一个 RTL 文件和一个 testbench 文件。")
    allowed_prefixes = ("rtl/", "tb/")
    normalized_files: dict[str, str] = {}
    total_size = 0
    for raw_path, content in files.items():
        if not isinstance(raw_path, str) or not isinstance(content, str):
            raise ValueError("模型返回文件映射格式错误。")
        normalized = raw_path.replace("\\", "/")
        candidate = Path(normalized)
        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or not normalized.startswith(allowed_prefixes)
            or any(not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", part) for part in candidate.parts)
        ):
            raise ValueError(f"拒绝写入工程目录外或受保护目录：{raw_path}")
        normalized = "/".join(candidate.parts)
        if len(normalized) > 240 or any(len(part) > 128 for part in candidate.parts):
            raise ValueError(f"模型文件路径超过允许长度：{raw_path}")
        if candidate.suffix.lower() not in {".v", ".sv"} or len(content.encode("utf-8")) > 256_000:
            raise ValueError(f"模型文件类型或大小不允许：{raw_path}")
        if normalized in normalized_files:
            raise ValueError(f"模型返回重复文件路径：{raw_path}")
        total_size += len(content.encode("utf-8"))
        if total_size > 1_000_000:
            raise ValueError("模型返回文件总大小超过 1,000,000 bytes。")
        normalized_files[normalized] = content.rstrip() + "\n"

    rtl_files = [p for p in normalized_files if p.startswith("rtl/")]
    tb_files = [p for p in normalized_files if p.startswith("tb/")]
    if not rtl_files or not tb_files:
        raise ValueError("模型返回结果必须同时包含 rtl/ 与 tb/ 文件。")
    joined_rtl = "\n".join(normalized_files[p] for p in rtl_files)
    uncommented_rtl = re.sub(r"/\*.*?\*/|//[^\r\n]*", " ", joined_rtl, flags=re.S)
    top_declarations = re.findall(rf"\bmodule\s+{re.escape(top)}\b", uncommented_rtl)
    if len(top_declarations) != 1:
        raise ValueError(f"RTL 文件中没有顶层模块 {top}。")
    ports, vector_ports = _ansi_top_ports(joined_rtl, top)
    board_io = payload.get("board_io", {})
    if not isinstance(board_io, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in board_io.items()):
        raise ValueError("模型返回的 board_io 必须是 top-level port 到板卡信号名的对象映射。")
    unknown_ports = set(board_io) - set(ports)
    if unknown_ports:
        raise ValueError(f"board_io 引用了顶层不存在的端口：{', '.join(sorted(unknown_ports))}")
    joined_tb = "\n".join(normalized_files[p] for p in tb_files)
    uncommented_tb = re.sub(r"/\*.*?\*/|//[^\r\n]*", " ", joined_tb, flags=re.S)
    tb_declarations = re.findall(rf"\bmodule\s+{re.escape(testbench_top)}\b", uncommented_tb)
    if len(tb_declarations) != 1:
        raise ValueError(f"testbench 文件中没有顶层模块 {testbench_top}。")
    if not re.search(r"\$fatal\b|\bassert\s*\(", uncommented_tb):
        raise ValueError("testbench 没有 $fatal 或 assert 检查，拒绝把空跑结果当作自检通过。")

    design_doc = payload.get("design_spec", "")
    if not isinstance(design_doc, str) or len(design_doc) > 40_000:
        raise ValueError("模型返回的设计说明无效或过长。")
    manifest = load_manifest(project)
    manifest["top_module"] = top
    manifest["testbench_top"] = testbench_top
    manifest["rtl_sources"] = rtl_files
    manifest["testbench_sources"] = tb_files
    board_path = project / "board" / "hx1p35a.json"
    board_profile = json.loads(board_path.read_text(encoding="utf-8"))
    if not isinstance(board_profile, dict) or not isinstance(board_profile.get("signals"), dict):
        raise ValueError(f"板卡资料格式无效：{board_path}")
    adc_text, unmapped = _render_adc(board_profile, ports, vector_ports, board_io)
    manifest["board_io"] = board_io
    manifest["board_constraints"] = "constraints/generated.adc"
    manifest["top_ports"] = ports
    manifest["unmapped_top_ports"] = unmapped
    manifest["td_full_allowed"] = not unmapped
    manifest["checks"] = payload.get("checks", []) if isinstance(payload.get("checks", []), list) else []
    generation=payload.get('_generation',{})
    manifest['model_backend']={key:generation[key] for key in ('provider','model') if isinstance(generation,dict) and isinstance(generation.get(key),str)}
    manifest["generated_by"] = 'model-response' if not manifest['model_backend'] else f"{manifest['model_backend'].get('model','unknown')} via {manifest['model_backend'].get('provider','unknown')}"
    writes = dict(normalized_files)
    writes["spec/design.md"] = design_doc.rstrip() + "\n"
    writes["constraints/generated.adc"] = adc_text
    writes["project.json"] = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    _atomic_write(project, writes)
