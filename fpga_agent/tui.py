"""IC Forge: a terminal workspace for conversational RTL design and verified tools."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from rich.markup import escape
from rich.syntax import Syntax
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Label, LoadingIndicator, Markdown, OptionList, RichLog, Select, Static, TextArea
from textual.widgets.option_list import Option

from .terminal import COMMANDS, PROVIDERS, ProcessRunner, Session, TaskPlan, Workspace, evidence, make_plan, read_json, redact, write_json

HELP = """### 在终端完成设计闭环
直接描述需求；使用 `@文件路径` 补充代码或规格。模型可以回答问题，也可以调用已有的设计、验证和交付工具。

| 命令 | 作用 |
| :--- | :--- |
| `/connect` · `/model name` | 连接模型、切换模型 |
| `/project` · `/init name` | 选择、创建工程 |
| `/design 需求` | 生成普通 RTL + 自检仿真 |
| `/design --full 需求` | 生成、仿真、TD 实现、交付 |
| `/cpu name multi --full` | 新建 RV32I CPU；架构 single / multi / pipeline |
| `/verify` · `/synth` · `/td` | 仿真、综合、完整实现 |
| `/flow` · `/package` | 完整流程、交付；支持 --simulation-only |
| `/suite --full` · `/replay --full` | CPU 基准、重放保存的 Luna 响应 |
| `/status` · `/artifacts` · `/open path` | 当前证据、产物、文件预览 |
| `/diff` | 查看 Git 工作区差异 |
| `/plan` · `/run` | 切换 Plan / Build、执行计划 |
| `/new` · `/resume` · `/export` | 会话管理与 Markdown 导出 |

**Enter** 发送 · **Shift+Enter** 换行 · **Tab** 补全斜杠命令 · **↑/↓** 历史输入
**Ctrl+P** 命令面板 · **Ctrl+O** 文件搜索 · **Ctrl+B** 项目栏 · **Ctrl+J** 日志 · **Shift+Tab** Plan / Build
**Esc** 取消运行 · **Ctrl+N** 新会话 · **Ctrl+Q** 退出

API Key 只在当前进程内存和子进程环境中使用。会话与设置保存在当前工作区 `.fpga-agent/`。
会读取工作区和当前工程的 AGENTS.md / CLAUDE.md / GEMINI.md 作为项目上下文。
CPU 使用独立参考验证；普通 RTL 使用生成的自检 testbench。板卡下载仍由 TD BitWriter 完成。
"""


class Prompt(TextArea):
    BINDINGS = [
        Binding("enter", "submit", show=False, priority=True),
        Binding("ctrl+enter", "submit", show=False, priority=True),
        Binding("shift+enter", "newline", show=False, priority=True),
        Binding("alt+enter", "newline", show=False, priority=True),
        Binding("tab", "complete", show=False, priority=True),
        Binding("up", "history_up", show=False, priority=True),
        Binding("down", "history_down", show=False, priority=True),
    ]

    class Submitted(Message):
        def __init__(self, text: str):
            self.text = text
            super().__init__()

    def __init__(self, **kwargs):
        super().__init__(show_line_numbers=False, highlight_cursor_line=False,
            placeholder="描述设计需求，或输入 / 查看命令 · @文件补充上下文", **kwargs)
        self.input_history: list[str] = []
        self.input_history_index = 0
        self.draft = ""

    def action_submit(self) -> None:
        if self.text.strip():
            self.post_message(self.Submitted(self.text.strip()))

    def action_newline(self) -> None:
        self.insert("\n")

    def action_complete(self) -> None:
        if self.text.startswith("/") and " " not in self.text:
            matches = [key for key in COMMANDS if key.startswith(self.text)]
            if matches:
                self.load_text(matches[0] + " ")
                self.move_cursor(self.document.end)
        elif re.search(r'@[^\s]*$', self.text):
            self.app.open_files(attach=True)

    def remember(self, text: str) -> None:
        if not self.input_history or self.input_history[-1] != text:
            self.input_history.append(text)
        self.input_history = self.input_history[-100:]
        self.input_history_index = len(self.input_history)
        self.load_text("")

    def action_history_up(self) -> None:
        if "\n" in self.text:
            self.action_cursor_up()
        elif self.input_history_index:
            if self.input_history_index == len(self.input_history):
                self.draft = self.text
            self.input_history_index -= 1
            self.load_text(self.input_history[self.input_history_index])
            self.move_cursor(self.document.end)

    def action_history_down(self) -> None:
        if "\n" in self.text:
            self.action_cursor_down()
        elif self.input_history_index < len(self.input_history):
            self.input_history_index += 1
            self.load_text(self.input_history[self.input_history_index] if self.input_history_index < len(self.input_history) else self.draft)
            self.move_cursor(self.document.end)


class MessageCard(Vertical):
    def __init__(self, role: str, text: str):
        super().__init__(classes="message " + role)
        self.role, self.text = role, text

    def compose(self) -> ComposeResult:
        names = {"user": "YOU", "assistant": "IC FORGE", "tool": "TOOL RESULT", "system": "WORKSPACE"}
        yield Static(names.get(self.role, self.role.upper()), classes="role")
        yield Markdown(self.text, open_links=False)


class Picker(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", show=False), Binding("down", "next", show=False, priority=True), Binding("up", "previous", show=False, priority=True)]

    def __init__(self, title: str, options: list[tuple[str, str]]):
        super().__init__()
        self.title_text, self.options = title, options

    def compose(self) -> ComposeResult:
        with Vertical(id="picker", classes="dialog"):
            yield Static(self.title_text, classes="dialog-title")
            yield Input(placeholder="搜索…", id="picker-search")
            yield OptionList(id="picker-options", markup=False)
            yield Static("↑ ↓ 选择   Enter 确认   Esc 返回", classes="muted")

    def on_mount(self) -> None:
        self.filter("")
        self.query_one(Input).focus()

    def filter(self, query: str) -> None:
        options = self.query_one(OptionList)
        options.clear_options()
        options.add_options([Option(label, id=value) for value, label in self.options if query.casefold() in label.casefold()])
        options.highlighted = 0 if options.option_count else None

    @on(Input.Changed)
    def search(self, event: Input.Changed) -> None:
        self.filter(event.value)

    @on(Input.Submitted)
    def choose(self) -> None:
        options = self.query_one(OptionList)
        if options.highlighted is not None:
            self.dismiss(options.get_option_at_index(options.highlighted).id)

    @on(OptionList.OptionSelected)
    def select(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_next(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_previous(self) -> None:
        self.query_one(OptionList).action_cursor_up()


class Connection(ModalScreen[dict | None]):
    BINDINGS = [Binding("escape", "dismiss(None)", show=False)]

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog", id="connection"):
            yield Static("CONNECT A MODEL", classes="dialog-title")
            yield Label("Provider")
            yield Select([(name, name) for name in PROVIDERS], value=self.settings.provider, allow_blank=False, id="provider")
            yield Label("Model")
            yield Input(value=self.settings.model, id="model")
            yield Label("Base URL · 空白使用环境变量或默认地址")
            yield Input(value=self.settings.base_url, placeholder="https://api.openai.com/v1", id="base-url")
            yield Label("API Key · 密码输入，仅当前会话内存有效")
            yield Input(password=True, placeholder="留空沿用环境变量中的 Key", id="api-key")
            yield Static("api / chat 使用 API Key；codex 使用本机 CLI 登录；exchange 只重放已有响应。", classes="muted")
            with Horizontal(classes="dialog-buttons"):
                yield Button("取消", id="connection-cancel")
                yield Button("保存连接", id="connection-save", variant="primary")

    @on(Button.Pressed)
    def save(self, event: Button.Pressed) -> None:
        if event.button.id == "connection-cancel":
            self.dismiss(None)
            return
        model = self.query_one("#model", Input).value.strip()
        base_url = self.query_one("#base-url", Input).value.strip().rstrip("/")
        parsed = urlsplit(base_url)
        if not model or (base_url and (parsed.scheme not in ("https", "http") or not parsed.netloc or parsed.username or parsed.query or parsed.fragment)):
            self.notify("填写模型名和有效的 HTTP(S) Base URL；URL 中不要包含密钥。", severity="error")
            return
        self.dismiss({"provider": self.query_one("#provider", Select).value, "model": model,
            "base_url": base_url, "api_key": self.query_one("#api-key", Input).value.strip()})


class Preview(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss(None)", "返回"), Binding("q", "dismiss(None)", show=False)]

    def __init__(self, title: str, text: str, lexer: str = "text"):
        super().__init__()
        self.title_text, self.text, self.lexer = title, text, lexer

    def compose(self) -> ComposeResult:
        with Vertical(id="preview", classes="dialog"):
            yield Static(self.title_text, markup=False, classes="dialog-title")
            with VerticalScroll():
                yield Static(Syntax(self.text, self.lexer, theme="dracula", line_numbers=True, word_wrap=True))
            yield Static("Esc / q 返回 · 支持鼠标滚动", classes="muted")


class ForgeApp(App):
    TITLE = "IC Forge"
    SUB_TITLE = "Digital IC design workspace"
    CSS_PATH = "tui.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+p", "palette", "命令", priority=True),
        Binding("ctrl+b", "sidebar", "项目", priority=True),
        Binding("ctrl+j", "logs", "日志", priority=True),
        Binding("ctrl+o", "files", "文件", priority=True),
        Binding("ctrl+n", "new_session", "新会话", priority=True),
        Binding("shift+tab", "mode", "Plan / Build", priority=True),
        Binding("escape", "cancel_task", "取消", priority=True),
        Binding("ctrl+c", "interrupt", show=False, priority=True),
        Binding("ctrl+q", "safe_quit", "退出", priority=True),
        Binding("f1", "help", show=False),
    ]

    def __init__(self, workspace: Path | str = ".", resume: str | None = None):
        super().__init__()
        self.workspace = Workspace(Path(workspace))
        self.settings = self.workspace.settings()
        self.session = self.workspace.resume(resume) if resume else Session(project=self.settings.project)
        self.selected: Path | None = None
        if self.session.project:
            try:
                candidate = self.workspace.resolve(self.session.project)
                if (candidate / "project.json").is_file():
                    self.selected = candidate
            except ValueError:
                pass
        self.busy = False
        self.runner: ProcessRunner | None = None
        self.pending: TaskPlan | None = None
        self.extra_env: dict[str, str] = {}
        self.started = 0.0
        self.task_title = "Ready"
        self.phase = ""
        self.tail: list[str] = []
        self.job_directory: Path | None = None
        self.project_paths: list[Path] = []

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static("[bold #63ead5]◈[/]  [bold]IC FORGE[/]  [#65718b]│[/]  [#a7b0c4]digital design workspace[/]", id="brand")
            yield Static(id="connection-status")
        with Horizontal(id="layout"):
            with Vertical(id="sidebar"):
                yield Static("WORKSPACE", classes="section-title")
                yield Static(self.workspace.root.name, id="workspace-name", markup=False)
                yield Static("PROJECTS", classes="section-title")
                yield OptionList(id="projects", markup=False)
                yield Button("＋  新建工程", id="new-project")
                yield Static("QUICK ACTIONS", classes="section-title")
                yield Button("▷  验证当前工程", id="quick-verify")
                yield Button("◇  完整实现交付", id="quick-flow")
                yield Static(id="session-info", classes="muted")
            with Vertical(id="center"):
                with Horizontal(id="conversation-bar"):
                    yield Static("CONVERSATION", id="conversation-title")
                    yield Static(id="project-badge")
                with VerticalScroll(id="transcript"):
                    with Vertical(id="welcome"):
                        yield Static("╭─ ◈ ─────────────────────────╮\n│   I C   F O R G E           │\n╰────────────────────────────╯", id="wordmark")
                        yield Static("从想法，到经过验证的 RTL。", id="welcome-title")
                        yield Static("在熟悉的终端里设计、验证、实现、交付。", id="welcome-subtitle")
                        with Horizontal(id="welcome-cards"):
                            yield Button("01  连接模型\n/connect", id="welcome-connect")
                            yield Button("02  选择工程\n/project", id="welcome-project")
                            yield Button("03  开始验证\n/verify", id="welcome-verify")
                        yield Markdown("直接输入需求，例如：**设计一个带溢出检测的 32 位计数器，并完成仿真验证。**\n\n已有工程可直接 `/verify`，无需模型 Key。`@rtl/core.v` 可附加文件。", id="welcome-note")
                yield RichLog(id="logs", highlight=False, markup=False, wrap=True, max_lines=2000)
                with Horizontal(id="task-row"):
                    yield LoadingIndicator(id="spinner")
                    yield Static("Ready · 所有验证工具可独立运行", id="task-status")
                yield Static(id="suggestions")
                with Vertical(id="composer"):
                    with Horizontal(id="composer-bar"):
                        yield Static(id="mode")
                        yield Static("Enter 发送   Shift+Enter 换行   / 命令   @ 文件", id="input-hint")
                    yield Prompt(id="prompt")
            with Vertical(id="inspector"):
                yield Static("DESIGN EVIDENCE", classes="section-title")
                yield Static(id="evidence")
                yield Static("PIPELINE", classes="section-title")
                yield Static("01   RTL / SPEC\n      ↓\n02   SIMULATION\n      ↓\n03   SYNTHESIS\n      ↓\n04   PLACE & ROUTE\n      ↓\n05   DELIVERY", id="pipeline")
                yield Static("TOOLS", classes="section-title")
                yield Static(id="tools")
                yield Static("Ctrl+P 打开命令面板\nF1 查看完整用法", classes="muted")
        yield Footer()

    async def on_mount(self) -> None:
        self.query_one("#logs").display = False
        self.query_one("#suggestions").display = False
        self.query_one("#spinner").display = False
        self.refresh_panels()
        for message in self.session.messages[-100:]:
            await self.render_message(message["role"], message["content"])
        self.query_one("#prompt", Prompt).input_history = [m["content"] for m in self.session.messages if m["role"] == "user"][-100:]
        self.query_one("#prompt", Prompt).input_history_index = len(self.query_one("#prompt", Prompt).input_history)
        self.set_interval(0.5, self.tick)
        self.query_one("#prompt").focus()

    def on_resize(self, event) -> None:
        if self.screen_stack:
            self.screen_stack[0].set_class(event.size.width < 125, "compact")
            self.screen_stack[0].set_class(event.size.width < 90, "narrow")

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self.extra_env)
        env.update({"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        package_root = str(Path(__file__).resolve().parent.parent)
        env["PYTHONPATH"] = package_root + os.pathsep + env.get("PYTHONPATH", "")
        if self.settings.base_url:
            env["OPENAI_BASE_URL"] = self.settings.base_url
        return env

    def model_ready(self) -> bool:
        env = self.environment()
        if self.settings.provider in ("api", "chat"):
            return bool(env.get("OPENAI_API_KEY") or env.get("FPGA_AGENT_API_KEY"))
        return self.settings.provider == "codex" and bool(shutil.which("codex"))

    def refresh_panels(self) -> None:
        ready = self.model_ready()
        self.query_one("#connection-status", Static).update(Text(f"{'●' if ready else '○'} {self.settings.provider} / {self.settings.model}", style="#63ead5" if ready else "#b8a3e8"))
        self.query_one("#mode", Static).update(f"[bold {'#63ead5' if self.settings.mode == 'build' else '#c3a3ff'}]{self.settings.mode.upper()}[/]  [#65718b]· Shift+Tab[/]")
        self.query_one("#project-badge", Static).update(Text(self.selected.name if self.selected else "No project selected", style="#a7b0c4"))
        self.query_one("#session-info", Static).update(Text(f"SESSION {self.session.id[:8]}\n{len(self.session.messages)} messages · auto saved", style="#65718b"))
        self.project_paths = self.workspace.projects()
        options = self.query_one("#projects", OptionList)
        options.clear_options()
        for i, project in enumerate(self.project_paths):
            selected = project == self.selected
            options.add_option(Option(Text(("▸ " if selected else "  ") + project.name, style="#63ead5" if selected else "#a7b0c4"), id=str(i)))
            if selected:
                options.highlighted = i
        if not self.project_paths:
            options.add_option(Option("暂无工程 · /init name", disabled=True))
        content = "[#65718b]选择工程后显示当前证据。[/]"
        if self.selected:
            data = evidence(self.selected)
            manifest = read_json(self.selected / "project.json", {})
            colors = {"current": "#63ead5", "missing": "#65718b", "stale": "#efc884", "failed": "#ff7f9b"}
            labels = {"current": "CURRENT", "missing": "NOT RUN", "stale": "STALE", "failed": "FAILED"}
            board = read_json(self.selected / "board/hx1p35a.json", {})
            hz = board.get("nominal_system_clock_hz")
            clock = f"{hz / 1e6:g} MHz nominal" if isinstance(hz, (int, float)) else "clock not specified"
            content = f"[bold]{escape(str(manifest.get('top_module', '?')))}[/]\n[#65718b]{escape(str(manifest.get('board', '?')))}\n{clock}[/]\n\n"
            for key, title in (("simulation", "SIM"), ("implementation", "TD")):
                state = data[key]
                content += f"[{colors[state]}]● {title:<5} {labels[state]}[/]\n"
            timing = data["timing"]
            if timing:
                content += "\n[#65718b]RECORDED WNS / ns[/]\n" + f"Setup   {timing.get('setup_wns_ns', '—')}\nHold    {timing.get('hold_wns_ns', '—')}\n"
            lut = data["resources"].get("lut6", {}).get("used")
            if lut is not None:
                content += f"\nLUT6    {lut:,}\n"
        self.query_one("#evidence", Static).update(content)
        from .td import find_td_command
        try:
            find_td_command()
            td = True
        except FileNotFoundError:
            td = False
        icarus = bool(shutil.which("iverilog") or (Path(__file__).resolve().parent.parent / ".tools/icarus/bin/iverilog.exe").is_file())
        self.query_one("#tools", Static).update(f"[{'#63ead5' if icarus else '#efc884'}]● Icarus Verilog[/]\n[{'#63ead5' if td else '#efc884'}]● Anlogic TD[/]\n[{'#63ead5' if ready else '#65718b'}]● Model {'configured' if ready else 'not connected'}[/]")
        self.refresh_pipeline()

    def refresh_pipeline(self) -> None:
        lines = []
        for index, (key, name) in enumerate((("rtl", "RTL / SPEC"), ("sim", "SIMULATION"), ("syn", "SYNTHESIS"), ("route", "PLACE & ROUTE"), ("package", "DELIVERY")), 1):
            color = "#63ead5" if self.busy and self.phase == key else "#8292b0"
            lines.append(f"[{color}]{index:02d}   {name}[/]")
            if index < 5:
                lines.append("[#43516a]      ↓[/]")
        self.query_one("#pipeline", Static).update("\n".join(lines))

    def tick(self) -> None:
        if self.busy:
            self.query_one("#task-status", Static).update(Text(f"{self.task_title}   {int(time.monotonic() - self.started)}s · Esc 取消", style="#63ead5"))

    async def render_message(self, role: str, text: str) -> None:
        self.query_one("#welcome").display = False
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(MessageCard(role, text))
        transcript.scroll_end(animate=False)

    async def add_message(self, role: str, text: str) -> None:
        text = redact(text, self.environment())
        self.session.messages.append({"role": role, "content": text, "at": time.time()})
        self.session.messages = self.session.messages[-100:]
        self.workspace.save_session(self.session)
        await self.render_message(role, text)
        self.query_one("#session-info", Static).update(Text(f"SESSION {self.session.id[:8]}\n{len(self.session.messages)} messages · auto saved", style="#65718b"))

    async def select_project(self, path: Path) -> None:
        if self.busy:
            raise ValueError("任务运行中；取消或等待完成后再切换工程。")
        path = self.workspace.resolve(str(path))
        if not (path / "project.json").is_file():
            raise ValueError("所选目录没有 project.json。")
        self.selected = path
        self.settings.project = path.relative_to(self.workspace.root).as_posix()
        self.session.project = self.settings.project
        self.workspace.save_settings(self.settings)
        self.workspace.save_session(self.session)
        self.pending = None
        self.refresh_panels()

    @on(OptionList.OptionSelected, "#projects")
    async def project_selected(self, event: OptionList.OptionSelected) -> None:
        try:
            await self.select_project(self.project_paths[int(event.option.id)])
            self.query_one("#prompt").focus()
        except ValueError as exc:
            self.notify(str(exc), severity="warning")

    @on(TextArea.Changed, "#prompt")
    def suggest(self) -> None:
        text = self.query_one("#prompt", Prompt).text
        widget = self.query_one("#suggestions", Static)
        matches = [(command, hint) for command, hint in COMMANDS.items() if command.startswith(text)] if text.startswith("/") and " " not in text else []
        widget.display = bool(matches)
        widget.update(Text("\n".join(f"{command:<13} {hint}" for command, hint in matches[:4]), style="#a7b0c4"))

    @on(Prompt.Submitted)
    async def prompt_submitted(self, event: Prompt.Submitted) -> None:
        text = redact(event.text, self.environment())
        if self.busy and text not in ("/cancel", "/quit", "/status", "/help"):
            self.notify("任务运行中。Esc 取消；完成后继续输入。", severity="warning")
            return
        self.query_one("#prompt", Prompt).remember(text)
        await self.add_message("user", text)
        try:
            await self.submit_command(text)
        except (ValueError, OSError, KeyError) as exc:
            await self.add_message("system", str(exc))

    async def submit_command(self, text: str) -> None:
        if not text.startswith("/"):
            local = {"运行仿真": "verify", "验证当前工程": "verify", "run tests": "verify", "完整实现交付": "flow", "生成交付包": "package"}
            if text in local:
                await self.offer_plan(make_plan(self.workspace, self.settings, local[text], {}, self.selected))
            else:
                self.start_chat(text)
            return
        command, _, rest = text.partition(" ")
        rest = rest.strip()
        tokens = [next((part for part in match if part), "") for match in re.findall(r'"([^"]*)"|\'([^\']*)\'|(\S+)', rest)] if rest else []
        if command in ("/help", "/?"):
            await self.add_message("assistant", HELP)
        elif command == "/connect":
            self.push_screen(Connection(self.settings), self.connected)
        elif command in ("/model", "/provider"):
            if not rest:
                await self.add_message("system", f"当前模型：`{self.settings.provider} / {self.settings.model}`。使用 `/connect` 配置，或 `{command} 值` 修改。")
            else:
                if command == "/provider" and rest not in PROVIDERS:
                    raise ValueError("Provider 必须是 api / chat / codex / exchange。")
                setattr(self.settings, command[1:], rest)
                self.workspace.save_settings(self.settings)
                self.refresh_panels()
                await self.add_message("system", "已更新模型设置；新任务使用该设置。")
        elif command == "/project":
            if rest:
                await self.select_project(self.workspace.resolve(rest.strip('"\'')))
                await self.add_message("system", "已选择工程：`" + self.session.project + "`。")
            else:
                self.push_screen(Picker("SELECT PROJECT", [(str(i), p.relative_to(self.workspace.root).as_posix()) for i, p in enumerate(self.project_paths)]), self.picked_project)
        elif command in ("/new", "/clear"):
            await self.new_session(clear=command == "/clear")
        elif command in ("/resume", "/sessions"):
            if rest:
                await self.load_session(rest)
            else:
                options = []
                for data in self.workspace.sessions():
                    title = next((m["content"][:50] for m in data.get("messages", []) if m.get("role") == "user"), "新会话")
                    options.append((data["id"], f"{data['id'][:8]}  {title}"))
                self.push_screen(Picker("RESUME SESSION", options), self.picked_session)
        elif command == "/plan":
            self.action_mode()
        elif command == "/run":
            if not self.pending:
                raise ValueError("没有待执行的计划。")
            plan, self.pending = self.pending, None
            self.start_plan(plan)
        elif command == "/status":
            data = evidence(self.selected) if self.selected else {"project": "尚未选择"}
            await self.add_message("assistant", "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```")
            self.refresh_panels()
        elif command == "/artifacts":
            if not self.selected:
                raise ValueError("先选择一个工程。")
            paths = list((self.selected / "deliverables").glob("*.zip")) + list((self.selected / "reports").glob("*.json"))
            report = read_json(self.selected / "reports/td.json", {})
            for item in report.get("bitstreams", []):
                path = self.workspace.resolve(item, base=self.selected)
                if path.is_file():
                    paths.append(path)
            await self.add_message("assistant", "\n".join(f"- `{p.relative_to(self.workspace.root).as_posix()}` · {p.stat().st_size:,} bytes" for p in paths) or "尚未生成产物。运行 `/verify` 或 `/flow`。")
        elif command == "/open":
            if not rest:
                raise ValueError("用法：/open rtl/core.v（相对当前工程）")
            path = self.workspace.resolve(rest.strip('"\'').lstrip("@"), base=self.selected or self.workspace.root)
            if not path.is_file() or path.stat().st_size > 512000 or path.name.startswith(".env") or ".git" in path.parts:
                raise ValueError("文件不存在、属于凭据文件或超过 512 KB。")
            text = redact(path.read_text(encoding="utf-8"), self.environment())
            lexer = {".v": "verilog", ".sv": "systemverilog", ".py": "python", ".json": "json", ".md": "markdown", ".tcl": "tcl"}.get(path.suffix, "text")
            self.push_screen(Preview(path.relative_to(self.workspace.root).as_posix(), text, lexer))
        elif command == "/files":
            self.open_files()
        elif command == "/export":
            path = self.workspace.resolve(rest.strip('"\'')) if rest else self.workspace.state / "exports" / f"{self.session.id}.md"
            if path.exists():
                raise ValueError("导出目标已存在，请使用新的文件名。")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# IC Forge session\n\n" + "\n\n".join(f"## {m['role']}\n\n{m['content']}" for m in self.session.messages), encoding="utf-8")
            await self.add_message("system", f"已导出：`{path.relative_to(self.workspace.root).as_posix()}`。")
        elif command == "/diff":
            self.start_plan(TaskPlan("Git diff", [["git", "diff", "--no-ext-diff", "--no-textconv", "--stat"], ["git", "diff", "--no-ext-diff", "--no-textconv"]]))
        elif command == "/cancel":
            await self.action_cancel_task()
        elif command in ("/quit", "/exit"):
            await self.action_safe_quit()
        else:
            action = command.lstrip("/")
            arguments: dict = {"full": "--full" in tokens}
            if action in ("init", "cpu"):
                if not tokens:
                    raise ValueError("用法：/init name 或 /cpu name multi --full")
                arguments["project"] = tokens[0]
                if action == "cpu":
                    action = "cpu-design"
                    arguments["architecture"] = tokens[1] if len(tokens) > 1 and not tokens[1].startswith("--") else "multi"
            elif action == "design":
                arguments["brief"] = rest.removeprefix("--full ")
            elif action == "package":
                arguments["simulation_only"] = "--simulation-only" in tokens
            plan = make_plan(self.workspace, self.settings, action, arguments, self.selected)
            await self.offer_plan(plan)

    async def offer_plan(self, plan: TaskPlan) -> None:
        if self.settings.mode == "plan":
            self.pending = plan
            steps = " → ".join(command[4] if len(command) > 4 and command[3] == "fpga_agent" else command[0] for command in plan.commands)
            await self.add_message("assistant", f"待执行：**{plan.title}**\n\n步骤：`{steps}`。输入 `/run` 执行。")
        else:
            self.start_plan(plan)

    async def connected(self, values: dict | None) -> None:
        if not values:
            return
        for name in ("provider", "model", "base_url"):
            setattr(self.settings, name, values[name])
        if values["api_key"]:
            self.extra_env["OPENAI_API_KEY"] = values["api_key"]
        self.workspace.save_settings(self.settings)
        self.refresh_panels()
        await self.add_message("system", "模型连接设置已保存。Key 仅保存在本次进程内存；连接状态表示配置就绪，首个请求会验证实际可用性。")

    async def picked_project(self, value: str | None) -> None:
        if value is not None:
            try:
                await self.select_project(self.project_paths[int(value)])
            except ValueError as exc:
                self.notify(str(exc), severity="warning")

    async def picked_session(self, value: str | None) -> None:
        if value:
            await self.load_session(value)

    def open_files(self, attach: bool = False) -> None:
        try:
            paths = self.workspace.files(self.selected)
        except (ValueError, OSError) as exc:
            self.notify(str(exc), severity="error")
            return
        callback = self.picked_attachment if attach else self.picked_file
        self.push_screen(Picker("ATTACH FILE" if attach else "OPEN FILE", [(p.relative_to(self.workspace.root).as_posix(), p.relative_to(self.workspace.root).as_posix()) for p in paths]), callback)

    async def picked_file(self, value: str | None) -> None:
        if value:
            path = self.workspace.resolve(value)
            relative = os.path.relpath(path, self.selected or self.workspace.root)
            try:
                await self.submit_command('/open "' + relative + '"')
            except (ValueError, OSError, UnicodeError) as exc:
                self.notify(str(exc), severity="error")

    async def picked_attachment(self, value: str | None) -> None:
        if value:
            path = self.workspace.resolve(value)
            relative = os.path.relpath(path, self.selected or self.workspace.root).replace("\\", "/")
            reference = '@"' + relative + '"' if " " in relative else "@" + relative
            prompt = self.query_one("#prompt", Prompt)
            prompt.load_text(re.sub(r'@[^\s]*$', lambda _: reference + " ", prompt.text))
            prompt.move_cursor(prompt.document.end)
            prompt.focus()

    async def load_session(self, value: str) -> None:
        if self.busy:
            raise ValueError("先完成或取消当前任务。")
        self.session = self.workspace.resume(value)
        self.selected = None
        if self.session.project:
            path = self.workspace.resolve(self.session.project)
            if (path / "project.json").is_file():
                self.selected = path
        self.pending = None
        await self.reset_transcript()
        for message in self.session.messages:
            await self.render_message(message["role"], message["content"])
        prompt = self.query_one("#prompt", Prompt)
        prompt.input_history = [m["content"] for m in self.session.messages if m["role"] == "user"]
        prompt.input_history_index = len(prompt.input_history)
        self.refresh_panels()

    async def reset_transcript(self) -> None:
        await self.query(".message").remove()
        self.query_one("#welcome").display = True
        self.query_one("#prompt", Prompt).load_text("")

    async def new_session(self, clear: bool = False) -> None:
        if self.busy:
            self.notify("先完成或取消当前任务。", severity="warning")
            return
        if clear:
            self.session.messages.clear()
        else:
            self.session = Session(project=self.session.project)
        self.workspace.save_session(self.session)
        self.pending = None
        await self.reset_transcript()
        self.query_one("#prompt", Prompt).input_history = []
        self.query_one("#prompt", Prompt).input_history_index = 0
        self.refresh_panels()
        self.query_one("#prompt").focus()

    def begin_job(self, title: str) -> None:
        if self.busy:
            raise ValueError("已有任务运行中。")
        self.busy = True
        self.started = time.monotonic()
        self.task_title = title
        self.phase = "rtl"
        self.tail = []
        self.runner = ProcessRunner()
        self.job_directory = self.workspace.state / "jobs" / uuid.uuid4().hex[:12]
        self.job_directory.mkdir(parents=True)
        self.query_one("#logs").display = True
        self.query_one("#logs", RichLog).clear()
        self.query_one("#spinner").display = True
        self.tick()
        self.refresh_pipeline()

    def output(self, line: str) -> None:
        line = redact(line, self.environment())
        self.query_one("#logs", RichLog).write(Text(line))
        self.tail = (self.tail + [line])[-20:]
        if "launch_runs syn_1" in line:
            self.phase = "syn"
            self.refresh_pipeline()
        elif "launch_runs phy_1" in line:
            self.phase = "route"
            self.refresh_pipeline()
        if self.job_directory:
            log = self.job_directory / "output.log"
            if not log.exists() or log.stat().st_size < 4_000_000:
                with log.open("a", encoding="utf-8") as stream:
                    stream.write(line + "\n")

    def finish_job(self, label: str) -> None:
        self.busy = False
        self.runner = None
        self.query_one("#spinner").display = False
        self.query_one("#task-status", Static).update(Text(label, style="#a7b0c4"))
        self.refresh_panels()

    def start_plan(self, plan: TaskPlan) -> None:
        self.begin_job(plan.title)
        self.pending = None
        self.run_worker(self.execute(plan), group="task", exit_on_error=False)

    async def execute(self, plan: TaskPlan) -> None:
        runner = self.runner
        assert runner is not None
        code = 1
        try:
            for i, command in enumerate(plan.commands):
                if runner.cancelled:
                    code = 130
                    break
                self.output(f"[{i+1}/{len(plan.commands)}] " + " ".join(command[:6]))
                operation = command[4] if len(command) > 4 and command[3] == "fpga_agent" else ""
                self.phase = {"verify": "sim", "flow": "sim", "cpu-suite": "sim", "td": "syn", "package": "package"}.get(operation, "rtl")
                self.refresh_pipeline()
                code = await runner.run(command, self.workspace.root, self.environment(), self.output)
                if code:
                    break
            cancelled = runner.cancelled
            label = "已取消" if cancelled else "通过" if code == 0 else f"失败 · exit {code}"
            body = f"**{plan.title} · {label}**\n\n"
            if code and not cancelled:
                body += "```text\n" + "\n".join(self.tail[-12:])[:6000] + "\n```\n"
            else:
                body += f"耗时 {time.monotonic()-self.started:.1f}s。详细输出见日志面板。"
            await self.add_message("tool", body)
            if code == 0 and plan.project and (plan.project / "project.json").is_file():
                # Selection is committed only after the operation actually succeeded.
                self.busy = False
                await self.select_project(plan.project)
        except asyncio.CancelledError:
            await runner.cancel()
            raise
        except Exception as exc:
            await self.add_message("system", "任务失败：" + str(exc))
        finally:
            self.finish_job("已取消" if runner.cancelled else "Ready · 任务已结束")

    def start_chat(self, text: str) -> None:
        if not self.model_ready():
            self.run_worker(self.add_message("assistant", "模型尚未连接。输入 `/connect` 配置 API Key 或本机 Codex CLI；`exchange` 只重放已有响应。\n\n已有工程可立即 `/verify`、`/flow` 或 `/replay --full`，这些命令不需要在线模型。"), exit_on_error=False)
            return
        context = self.workspace.context(self.selected, text)
        def clean(value):
            if isinstance(value, str):
                return redact(value, self.environment())
            if isinstance(value, dict):
                return {key: clean(item) for key, item in value.items()}
            if isinstance(value, list):
                return [clean(item) for item in value]
            return value
        context = clean(context)
        self.begin_job("Model · " + self.settings.model)
        assert self.job_directory is not None
        request = {"workspace": str(self.workspace.root), "provider": self.settings.provider,
            "model": self.settings.model, "mode": self.settings.mode, "context": context,
            "messages": self.session.messages[-16:]}
        write_json(self.job_directory / "request.json", request)
        self.run_worker(self.chat(request), group="task", exit_on_error=False)

    async def chat(self, request: dict) -> None:
        runner = self.runner
        directory = self.job_directory
        assert runner is not None and directory is not None
        plan = None
        try:
            code = await runner.run([sys.executable, "-u", "-m", "fpga_agent.tui_worker", str(directory / "request.json")], self.workspace.root, self.environment(), self.output)
            if runner.cancelled:
                await self.add_message("system", "模型请求已取消。")
            elif code:
                await self.add_message("system", "模型请求失败，未执行工具。\n\n```text\n" + "\n".join(self.tail[-10:])[:5000] + "\n```")
            else:
                result = read_json(directory / "response.json")
                if not isinstance(result, dict):
                    raise ValueError("模型没有返回可用计划。")
                await self.add_message("assistant", result.get("message", ""))
                if result.get("action", "none") != "none":
                    plan = make_plan(self.workspace, self.settings, result["action"], result["arguments"], self.selected)
                    if request["mode"] == "plan":
                        self.pending = plan
                        plan = None
                        await self.add_message("system", "计划已就绪，输入 `/run` 执行。")
        except asyncio.CancelledError:
            await runner.cancel()
            raise
        except Exception as exc:
            await self.add_message("system", "无法使用该模型响应：" + str(exc))
        finally:
            self.finish_job("Ready · 模型请求已结束")
        if plan:
            self.start_plan(plan)

    async def action_cancel_task(self) -> None:
        if self.runner:
            self.query_one("#task-status", Static).update("正在终止任务及子进程…")
            await self.runner.cancel()

    async def action_interrupt(self) -> None:
        if self.busy:
            await self.action_cancel_task()
        else:
            self.query_one("#prompt", Prompt).load_text("")

    async def action_safe_quit(self) -> None:
        await self.action_cancel_task()
        self.workspace.save_session(self.session)
        self.exit()

    def action_palette(self) -> None:
        self.push_screen(Picker("COMMAND PALETTE", [(command, command + "   " + label) for command, label in COMMANDS.items()]), self.picked_command)

    async def picked_command(self, command: str | None) -> None:
        if not command:
            return
        if command in ("/init", "/cpu", "/design", "/open", "/model", "/provider", "/export"):
            self.query_one("#prompt", Prompt).load_text(command + " ")
            self.query_one("#prompt").focus()
        else:
            try:
                if self.busy and command not in ("/cancel", "/help", "/status", "/quit"):
                    self.notify("任务运行中。", severity="warning")
                    return
                await self.submit_command(command)
            except (ValueError, OSError) as exc:
                await self.add_message("system", str(exc))

    def action_sidebar(self) -> None:
        widget = self.query_one("#sidebar")
        widget.display = not widget.display

    def action_logs(self) -> None:
        widget = self.query_one("#logs")
        widget.display = not widget.display

    def action_files(self) -> None:
        self.open_files()

    def action_mode(self) -> None:
        self.settings.mode = "plan" if self.settings.mode == "build" else "build"
        self.workspace.save_settings(self.settings)
        self.refresh_panels()

    async def action_new_session(self) -> None:
        await self.new_session()

    async def action_help(self) -> None:
        await self.add_message("assistant", HELP)

    @on(Button.Pressed)
    async def button(self, event: Button.Pressed) -> None:
        commands = {"welcome-connect": "/connect", "welcome-project": "/project", "welcome-verify": "/verify", "quick-verify": "/verify", "quick-flow": "/flow"}
        if self.busy:
            self.notify("任务运行中。Esc 取消。", severity="warning")
            return
        if event.button.id == "new-project":
            self.query_one("#prompt", Prompt).load_text("/init ")
            self.query_one("#prompt").focus()
        elif event.button.id in commands:
            try:
                await self.submit_command(commands[event.button.id])
            except (ValueError, OSError) as exc:
                await self.add_message("system", str(exc))


def launch(workspace: str = ".", resume: str | None = None) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("IC Forge 需要交互式终端。请在 PowerShell / Windows Terminal 中运行 fpga-agent tui；脚本使用 fpga-agent --help。", file=sys.stderr)
        return 2
    ForgeApp(workspace, resume).run()
    return 0
