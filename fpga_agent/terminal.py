"""Workspace state, typed operations and cancellable processes for the terminal UI."""
from __future__ import annotations

import asyncio
import codecs
import json
import os
import re
import signal
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PROVIDERS = ("api", "chat", "codex", "exchange")
COMMANDS = {
    "/help": "快捷键与命令帮助", "/connect": "连接模型 / API 配置",
    "/project": "切换工程", "/init": "创建工程：/init name",
    "/design": "生成 RTL：/design 需求描述", "/cpu": "生成 CPU：/cpu name multi --full",
    "/verify": "独立仿真验证", "/synth": "TD 逻辑综合", "/td": "TD 综合与布局布线",
    "/flow": "仿真 → TD → 交付", "/package": "生成交付包", "/suite": "三个 CPU 回归",
    "/replay": "重放当前 CPU 的 Luna 响应", "/status": "查看证据与工具状态",
    "/artifacts": "浏览交付与报告", "/files": "搜索与预览工程文件", "/open": "预览文件：/open rtl/core.v",
    "/diff": "查看 Git 差异", "/model": "切换模型：/model name",
    "/provider": "切换接口：api / chat / codex / exchange",
    "/plan": "切换 Plan / Build", "/run": "执行待运行计划",
    "/new": "新建会话", "/resume": "恢复会话", "/clear": "清空当前对话",
    "/export": "导出 Markdown 会话", "/cancel": "取消当前任务", "/quit": "退出",
}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def redact(text: str, environment: dict[str, str] | None = None) -> str:
    for key, value in (environment or {}).items():
        if value and any(token in key.upper() for token in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")):
            text = text.replace(value, "[redacted]")
    text = re.sub(r"\b(?:sk-(?:proj-)?[\w-]{20,}|gh[pousr]_[\w]{20,}|github_pat_[\w]{20,})", "[redacted]", text)
    return text


@dataclass
class Settings:
    provider: str = "api"
    model: str = "gpt-6-luna"
    base_url: str = ""
    project: str = ""
    mode: str = "build"


@dataclass
class Session:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=timestamp)
    updated_at: str = field(default_factory=timestamp)
    project: str = ""
    messages: list[dict] = field(default_factory=list)


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.state = self.root / ".fpga-agent"
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, value: str, *, base: Path | None = None) -> Path:
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else (base or self.root) / path).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError("路径必须在当前工作区内。用 --workspace 打开其它目录。")
        return path

    def settings(self) -> Settings:
        data = read_json(self.state / "config.json", {})
        if not isinstance(data, dict):
            return Settings()
        result = Settings(**{key: value for key, value in data.items() if key in Settings.__dataclass_fields__ and isinstance(value, str)})
        if result.provider not in PROVIDERS:
            result.provider = "api"
        if result.mode not in ("build", "plan"):
            result.mode = "build"
        return result

    def save_settings(self, settings: Settings) -> None:
        write_json(self.state / "config.json", asdict(settings))

    def save_session(self, session: Session) -> None:
        session.updated_at = timestamp()
        write_json(self.state / "sessions" / f"{session.id}.json", asdict(session))

    def sessions(self) -> list[dict]:
        values = [read_json(path) for path in (self.state / "sessions").glob("*.json")]
        return sorted([x for x in values if isinstance(x, dict) and x.get("id")], key=lambda x: x.get("updated_at", ""), reverse=True)

    def resume(self, session_id: str = "latest") -> Session:
        for data in self.sessions():
            if session_id == "latest" or data["id"] == session_id:
                if not re.fullmatch(r"[0-9a-f]{12}", data["id"]) or not isinstance(data.get("messages"), list):
                    raise ValueError("会话文件格式无效。")
                if not all(isinstance(m, dict) and m.get("role") in ("user", "assistant", "system", "tool") and isinstance(m.get("content"), str) for m in data["messages"]):
                    raise ValueError("会话消息格式无效。")
                return Session(**{key: value for key, value in data.items() if key in Session.__dataclass_fields__})
        raise ValueError("没有找到可恢复的会话。")

    def projects(self) -> list[Path]:
        result = []
        # Bounded discovery never walks TD build trees or dependency directories.
        candidates = [self.root / "project.json"]
        candidates.extend(self.root.glob("*/project.json"))
        candidates.extend(self.root.glob("benchmarks/*/project.json"))
        for path in candidates:
            if path.is_file() and not any(p.startswith(".") for p in path.relative_to(self.root).parts):
                if isinstance(read_json(path), dict):
                    try:
                        result.append(self.resolve(str(path.parent)))
                    except ValueError:
                        continue
        return sorted(set(result))

    def context(self, project: Path | None, text: str = "") -> dict:
        result: dict = {"workspace": self.root.name, "projects": [p.relative_to(self.root).as_posix() for p in self.projects()]}
        instructions = {}
        for directory in dict.fromkeys([self.root, project]):
            if directory is None:
                continue
            for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
                path = self.resolve(name, base=directory)
                if path.is_file() and path.stat().st_size < 64000:
                    instructions[path.relative_to(self.root).as_posix()] = path.read_text(encoding="utf-8", errors="replace")[:12000]
        result["project_instructions"] = instructions
        if project:
            result["project"] = project.relative_to(self.root).as_posix()
            result["manifest"] = read_json(project / "project.json", {})
            result["evidence"] = evidence(project)
            sources = {}
            manifest = result["manifest"]
            names = list(manifest.get("rtl_sources", [])) + ["spec/brief.md", "spec/design.md"]
            budget = 36000
            for name in names:
                path = self.resolve(name, base=project)
                if path.is_file() and budget > 0:
                    content = path.read_text(encoding="utf-8", errors="replace")[:min(12000, budget)]
                    sources[name] = content
                    budget -= len(content)
            result["sources"] = sources
        attached = {}
        for match in re.finditer(r'@(?:"([^"]+)"|([^\s]+))', text):
            value = match.group(1) or match.group(2)
            path = self.resolve(value, base=project or self.root)
            if not path.is_file() or path.stat().st_size > 64000:
                raise ValueError(f"附件不存在或超过 64 KB：{value}")
            if any(part in {".git", ".fpga-agent"} for part in path.relative_to(self.root).parts) or path.name.startswith(".env"):
                raise ValueError("附件不接受凭据文件或 agent 内部状态。")
            if len(attached) >= 8:
                raise ValueError("一次最多附加 8 个文件。")
            attached[value] = path.read_text(encoding="utf-8", errors="strict")
        result["attachments"] = attached
        return result

    def files(self, project: Path | None) -> list[Path]:
        allowed = {".v", ".sv", ".md", ".json", ".py", ".tcl", ".sdc", ".adc", ".txt", ".toml", ".yaml", ".yml"}
        paths = [p for p in self.root.iterdir() if p.is_file() and p.suffix.lower() in allowed and not p.name.startswith(".")]
        if project:
            for directory in ("rtl", "tb", "spec", "board", "constraints", "reports"):
                for path in (project / directory).glob("**/*"):
                    if path.is_file() and path.suffix.lower() in allowed:
                        paths.append(self.resolve(str(path)))
            paths.extend(p for p in project.iterdir() if p.is_file() and p.suffix.lower() in allowed and not p.name.startswith("."))
        return sorted(set(paths))[:300]


def evidence(project: Path) -> dict:
    from .evidence import implementation_valid, simulation_valid
    manifest = read_json(project / "project.json", {})
    sim = read_json(project / "reports/simulation.json", {})
    td = read_json(project / "reports/td.json", {})
    result = {"simulation": "missing", "implementation": "missing", "timing": td.get("timing_summary") or {}, "resources": td.get("resource_summary") or {}}
    for key, report, validator in (("simulation", sim, simulation_valid), ("implementation", td, implementation_valid)):
        try:
            valid, reason = validator(project, manifest, report)
            result[key] = "current" if valid else ("failed" if report.get("status") == "fail" else "stale" if report else "missing")
            result[key + "_reason"] = reason
        except (ValueError, KeyError, OSError, TypeError) as exc:
            result[key] = "stale"
            result[key + "_reason"] = str(exc)
    return result


@dataclass
class TaskPlan:
    title: str
    commands: list[list[str]]
    project: Path | None = None


def make_plan(workspace: Workspace, settings: Settings, action: str, arguments: dict, selected: Path | None) -> TaskPlan:
    """Convert typed tools to argv. Model output is never shell or Python code."""
    if not isinstance(arguments, dict):
        raise ValueError("工具参数必须是对象。")
    full = arguments.get("full", False)
    if not isinstance(full, bool):
        raise ValueError("full 必须是布尔值。")
    project_value = arguments.get("project")
    if project_value is not None and not isinstance(project_value, str):
        raise ValueError("project 必须是路径字符串。")
    project = workspace.resolve(project_value) if project_value else workspace.resolve(str(selected)) if selected else None
    cli = [sys.executable, "-u", "-m", "fpga_agent"]
    commands = []
    if action == "suite":
        commands = [cli + ["cpu-suite", "--root", str(workspace.root / "benchmarks")] + (["--full"] if full else [])]
        return TaskPlan("RISC-V benchmark suite", commands)
    if action not in {"init", "design", "cpu-design", "verify", "synth", "td", "flow", "package", "replay"}:
        raise ValueError(f"不支持的工具：{action}")
    if not project:
        raise ValueError("先使用 /project 选择工程，或 /init name 创建工程。")
    if action in {"init", "design", "cpu-design"}:
        from .project import safe_project_name
        safe_project_name(project.name)
    if action == "init":
        commands = [cli + ["init", project.name, "--directory", str(project.parent)]]
    elif action in {"cpu-design", "replay"}:
        architecture = arguments.get("architecture", "multi")
        if action == "replay":
            architecture = read_json(project / "project.json", {}).get("model_backend", {}).get("architecture", "multi")
        if architecture not in {"single", "multi", "pipeline"}:
            raise ValueError("CPU 架构必须是 single、multi 或 pipeline。")
        command = cli + ["cpu-design", str(project), "--architecture", architecture,
                         "--provider", "exchange" if action == "replay" else settings.provider, "--model", settings.model]
        if action == "replay":
            response = project / "generation/luna-response.json"
            if not response.is_file():
                raise ValueError("当前工程没有 generation/luna-response.json。")
            envelope = read_json(response, {})
            command[-1] = envelope.get("model", settings.model)
            command += ["--response-file", str(response)]
        if full:
            command.append("--full")
        commands = [command]
    elif action == "design":
        brief = arguments.get("brief", "")
        if not isinstance(brief, str) or not brief.strip() or len(brief) > 60000:
            raise ValueError("设计需求不能为空，且不能超过 60,000 字符。")
        if not (project / "project.json").is_file():
            commands.append(cli + ["init", project.name, "--directory", str(project.parent)])
        commands.append(cli + ["design", str(project), "--brief", brief, "--provider", settings.provider, "--model", settings.model])
        if full:
            commands.append(cli + ["flow", str(project)])
    else:
        if not (project / "project.json").is_file():
            raise ValueError(f"没有找到工程：{project.name}")
        if action in {"td", "synth"}:
            commands = [cli + ["td", str(project), "--stage", "synth" if action == "synth" else "full"]]
        else:
            commands = [cli + [action, str(project)]]
            if action == "package" and arguments.get("simulation_only") is True:
                commands[0].append("--simulation-only")
    return TaskPlan(action + " · " + project.name, commands, project)


class ProcessRunner:
    def __init__(self):
        self.process: asyncio.subprocess.Process | None = None
        self.cancelled = False

    async def run(self, argv: list[str], cwd: Path, env: dict[str, str], on_output: Callable[[str], None]) -> int:
        if self.cancelled:
            return 130
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        self.process = await asyncio.create_subprocess_exec(*argv, cwd=cwd, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **options)
        try:
            if self.cancelled:
                await self.cancel()
            assert self.process.stdout is not None
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            pending = ""
            while chunk := await self.process.stdout.read(4096):
                pending += decoder.decode(chunk)
                lines = pending.split("\n")
                pending = lines.pop()
                for line in lines:
                    on_output(redact(line.rstrip("\r"), env))
                # Preserve a tail so an incomplete credential/token cannot straddle
                # a cut when a tool emits a very long line without newlines.
                if len(pending) > 16384:
                    pending = redact(pending, env)
                    on_output(pending[:-1024])
                    pending = pending[-1024:]
            pending += decoder.decode(b"", final=True)
            if pending:
                on_output(redact(pending.rstrip("\r"), env))
            return await self.process.wait()
        finally:
            if self.process and self.process.returncode is None:
                await self.cancel()
            self.process = None

    async def cancel(self) -> None:
        self.cancelled = True
        process = self.process
        if process is None or process.returncode is not None:
            return
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            await killer.wait()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            await process.wait()
