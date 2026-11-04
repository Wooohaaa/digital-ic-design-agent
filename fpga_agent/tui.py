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

from rich.syntax import Syntax
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Markdown, OptionList, RichLog, Select, Static, TextArea
from textual.widgets.option_list import Option

from .terminal import COMMANDS, PROVIDERS, ProcessRunner, Session, TaskPlan, Workspace, evidence, make_plan, read_json, redact, write_json

# FIGlet Fraktur lettering rendered from pyfiglet's Fraktur font (xero/figlet-fonts).
FRAKTUR_WORDMARK = r'''    .....     .        ...                 .....                  ....              ..      ...           ....        .         ..      .
  .d88888Neu. 'L    xH88"`~ .x8X        .H8888888x.  '`+      .x~X88888Hx.       :~"8888x :"%888x      .x88" `^x~  xH(`      x88f` `..x88. .>
  F""""*8888888F  :8888   .f"8888Hf    :888888888888x.  !    H8X 888888888h.    8    8888Xf  8888>    X888   x8 ` 8888h    :8888   xf`*8888%
 *      `"*88*"  :8888>  X8L  ^""`     8~    `"*88888888"   8888:`*888888888:  X88x. ?8888k  8888X   88888  888.  %8888   :8888f .888  `"`
  -....    ue=:. X8888  X888h          !      .  `f""""     88888:        `%8  '8888L'8888X  '%88X  <8888X X8888   X8?    88888' X8888. >"8x
         :88N  ` 88888  !88888.         ~:...-` :8L <)88: . `88888          ?>  "888X 8888X:xnHH(`` X8888> 488888>"8888x  88888  ?88888< 888>
         9888L   88888   %88888            .   :888:>X88! `. ?888%           X    ?8~ 8888X X8888   X8888>  888888 '8888L 88888   "88888 "8%
  uzu.   `8888L  88888 '> `8888>        :~"88x 48888X ^`    ~*??.            >  -~`   8888> X8888   ?8888X   ?8888>'8888X 88888 '  `8888>
,""888i   ?8888  `8888L %  ?888   !    <  :888k'88888X     .x88888h.        <   :H8x  8888  X8888    8888X h  8888 '8888~ `8888> %  X88!
4  9888L   %888>  `8888  `-*""   /       d8888f '88888X   :"""8888888x..  .x    8888> 888~  X8888     ?888  -:8*"  <888"   `888X  `~""`   :
'  '8888   '88%     "888.      :"       :8888!    ?8888>  `    `*888888888"     48`" '8*~   `8888!`    `*88.      :88%       "88k.      .~
     "*8Nu.z*"        `""***~"`         X888!      8888~          ""***""        ^-==""      `""          ^"~====""`           `""*==~~`
                                        '888       X88f
                                         '%8:     .8*"
                                            ^----~"`
'''

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
**Ctrl+P** 命令面板 · **Ctrl+O** 文件搜索 · **Ctrl+B** 选择工程 · **Ctrl+J** 日志 · **Shift+Tab** 模式
**Esc** 取消运行 · **Ctrl+N** 新会话 · **Ctrl+Q** 退出
任务运行时继续输入会自动排队，当前任务结束后依次执行。

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
            placeholder="描述设计需求，或输入 / 查看命令", **kwargs)
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
        glyphs = {"user": "❯", "assistant": "☤", "tool": "🔧", "system": "·"}
        with Horizontal(classes="message-row"):
            yield Static(glyphs.get(self.role, "·"), classes="message-glyph")
            with Vertical(classes="message-content"):
                if self.role == "assistant":
                    yield Static("ℑℭ 𝔉𝔬𝔯𝔤𝔢", classes="message-title")
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
        Binding("ctrl+j", "logs", "日志", show=False, priority=True),
        Binding("ctrl+o", "files", "文件", show=False, priority=True),
        Binding("ctrl+n", "new_session", "新会话", show=False, priority=True),
        Binding("shift+tab", "mode", "模式", priority=True),
        Binding("escape", "cancel_task", "取消", show=False, priority=True),
        Binding("ctrl+c", "interrupt", show=False, priority=True),
        Binding("ctrl+q", "safe_quit", "退出", priority=True),
        Binding("f1", "help", show=False),
    ]

    def __init__(self, workspace: Path | str = ".", resume: str | None = None):
        super().__init__()
        self.workspace = Workspace(Path(workspace))
        self.settings = self.workspace.settings()
        self.session = self.workspace.resume(resume) if resume else Session(project=self.settings.project)
        # A prior process may have closed with prompts still queued. Treat them as
        # ordinary conversation history when resuming; only the in-memory queue runs work.
        for message in self.session.messages:
            message.pop("queue_id", None)
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
        self.tail: list[str] = []
        self.job_directory: Path | None = None
        self.project_paths: list[Path] = []
        self.queued_input: list[dict[str, str]] = []
        self.processing_queue = False
        self.task_status_generation = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="app-shell"):
            with VerticalScroll(id="transcript"):
                with Vertical(id="welcome"):
                    yield Static(FRAKTUR_WORDMARK, id="wordmark", markup=False)
                    yield Static("ℑℭ 𝔉𝔬𝔯𝔤𝔢", id="wordmark-compact", markup=False)
                    yield Static("数字 IC 设计 · 仿真 · FPGA 实现", id="welcome-title")
                    yield Static(f"{self.settings.provider} / {self.settings.model} · {self.workspace.root.name}", id="welcome-meta")
                    yield Static("输入需求 · /help · Ctrl+P 命令 · Ctrl+B 工程 · Ctrl+J 日志 · @路径文件", id="welcome-note")
            yield Static(id="task-status")
            yield RichLog(id="logs", highlight=False, markup=False, wrap=True, max_lines=2000)
            yield Static(id="suggestions")
            with Horizontal(id="composer"):
                yield Static("❯", id="prompt-glyph")
                yield Prompt(id="prompt")
            yield Static(id="status-line")

    async def on_mount(self) -> None:
        self.query_one("#logs").display = False
        self.query_one("#suggestions").display = False
        self.refresh_panels()
        for message in self.session.messages[-100:]:
            await self.render_message(message["role"], message["content"])
        self.query_one("#prompt", Prompt).input_history = [m["content"] for m in self.session.messages if m["role"] == "user"][-100:]
        self.query_one("#prompt", Prompt).input_history_index = len(self.query_one("#prompt", Prompt).input_history)
        self.set_interval(0.5, self.tick)
        self.set_interval(0.3, self.process_queued_input)
        self.query_one("#prompt").focus()

    def on_resize(self, event) -> None:
        if self.screen_stack:
            self.screen_stack[0].set_class(event.size.width < 125, "compact")
            self.screen_stack[0].set_class(event.size.width < 145, "narrow")

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
        """Refresh the compact context line and the project list used by pickers."""
        ready = self.model_ready()
        self.project_paths = self.workspace.projects()
        status = Text()
        status.append("● " if ready else "○ ", style="#55aaff" if ready else "#efc884")
        status.append(f"{self.settings.provider}/{self.settings.model}", style="#c6d0df")
        status.append(f"  ·  {self.settings.mode.upper()}", style="#8998a9")
        status.append(f"  ·  {self.selected.name if self.selected else self.workspace.root.name}", style="#8998a9")
        if self.queued_input:
            status.append(f"  ·  {len(self.queued_input)} queued", style="#efc884")
        self.query_one("#status-line", Static).update(status)
        self.query_one("#welcome-meta", Static).update(
            f"{self.settings.provider} / {self.settings.model} · {self.workspace.root.name}"
        )

    def tick(self) -> None:
        if self.busy:
            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            frame = frames[int(time.monotonic() * 3) % len(frames)]
            self.query_one("#task-status", Static).update(
                Text(f"{frame}  {self.task_title} · {int(time.monotonic() - self.started)}s · Esc 取消 · Ctrl+J 日志", style="#55aaff")
            )

    async def render_message(self, role: str, text: str) -> None:
        self.query_one("#welcome").display = False
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(MessageCard(role, text))
        transcript.scroll_end(animate=False)

    async def add_message(self, role: str, text: str, queue_id: str | None = None) -> None:
        text = redact(text, self.environment())
        message = {"role": role, "content": text, "at": time.time()}
        if queue_id:
            message["queue_id"] = queue_id
        self.session.messages.append(message)
        self.session.messages = self.session.messages[-100:]
        self.workspace.save_session(self.session)
        await self.render_message(role, text)

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

    @on(TextArea.Changed, "#prompt")
    def suggest(self) -> None:
        text = self.query_one("#prompt", Prompt).text
        widget = self.query_one("#suggestions", Static)
        matches = [(command, hint) for command, hint in COMMANDS.items() if command.startswith(text)] if text.startswith("/") and " " not in text else []
        widget.display = bool(matches)
        widget.update(Text("\n".join(f"{command:<13} {hint}" for command, hint in matches[:4]), style="#b9c6d5"))

    @on(Prompt.Submitted)
    async def prompt_submitted(self, event: Prompt.Submitted) -> None:
        text = redact(event.text, self.environment())
        if not text:
            return
        self.query_one("#prompt", Prompt).remember(text)
        command = text.partition(" ")[0]
        if self.busy and command not in ("/cancel", "/quit", "/exit", "/status", "/help", "/?"):
            queue_id = uuid.uuid4().hex
            self.queued_input.append({"id": queue_id, "text": text})
            await self.add_message("user", text, queue_id=queue_id)
            self.notify(f"已排队（{len(self.queued_input)}）。任务结束后自动继续。", timeout=3)
            self.refresh_panels()
            return
        await self.handle_prompt_text(text)

    async def handle_prompt_text(self, text: str, recorded: bool = False) -> None:
        if not recorded:
            await self.add_message("user", text)
        try:
            await self.submit_command(text)
        except (ValueError, OSError, KeyError) as exc:
            await self.add_message("system", str(exc))

    async def process_queued_input(self) -> None:
        """Run one queued prompt when no job or modal screen is active."""
        if self.processing_queue or self.busy or not self.queued_input or len(self.screen_stack) > 1:
            return
        self.processing_queue = True
        item = self.queued_input.pop(0)
        text = item["text"]
        for message in self.session.messages:
            if message.get("queue_id") == item["id"]:
                message.pop("queue_id", None)
                break
        self.workspace.save_session(self.session)
        self.refresh_panels()
        try:
            await self.handle_prompt_text(text, recorded=True)
        finally:
            self.processing_queue = False

    async def submit_command(self, text: str) -> None:
        if not text.startswith("/"):
            local = {"运行仿真": "verify", "验证当前工程": "verify", "run tests": "verify", "完整实现交付": "flow", "生成交付包": "package"}
            if text in local:
                await self.offer_plan(make_plan(self.workspace, self.settings, local[text], {}, self.selected))
            else:
                await self.start_chat(text)
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
        self.task_status_generation += 1
        self.started = time.monotonic()
        self.task_title = title
        self.tail = []
        self.runner = ProcessRunner()
        self.job_directory = self.workspace.state / "jobs" / uuid.uuid4().hex[:12]
        self.job_directory.mkdir(parents=True)
        self.query_one("#logs", RichLog).clear()
        self.query_one("#task-status").display = True
        self.tick()
        self.refresh_panels()

    def output(self, line: str) -> None:
        line = redact(line, self.environment())
        self.query_one("#logs", RichLog).write(Text(line))
        self.tail = (self.tail + [line])[-20:]
        if self.job_directory:
            log = self.job_directory / "output.log"
            if not log.exists() or log.stat().st_size < 4_000_000:
                with log.open("a", encoding="utf-8") as stream:
                    stream.write(line + "\n")

    def finish_job(self, label: str) -> None:
        self.busy = False
        self.runner = None
        self.task_status_generation += 1
        generation = self.task_status_generation
        widget = self.query_one("#task-status", Static)
        widget.update(Text(f"✓ {label} · {time.monotonic() - self.started:.1f}s", style="#8998a9"))
        widget.display = True
        self.set_timer(2.5, lambda: self.clear_task_status(generation))
        self.refresh_panels()

    def clear_task_status(self, generation: int) -> None:
        if generation == self.task_status_generation and not self.busy:
            self.query_one("#task-status").display = False

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
            self.finish_job("已取消" if runner.cancelled else "任务已结束")

    async def start_chat(self, text: str) -> None:
        if not self.model_ready():
            await self.add_message("assistant", "模型尚未连接。输入 `/connect` 配置 API Key 或本机 Codex CLI；`exchange` 只重放已有响应。\n\n已有工程可立即 `/verify`、`/flow` 或 `/replay --full`，这些命令不需要在线模型。")
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
            "messages": [message for message in self.session.messages[-16:] if not message.get("queue_id")]}
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
            self.finish_job("模型请求结束")
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

    async def action_sidebar(self) -> None:
        await self.submit_command("/project")

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

def launch(workspace: str = ".", resume: str | None = None) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print("IC Forge 需要交互式终端。请在 PowerShell / Windows Terminal 中运行 fpga-agent tui；脚本使用 fpga-agent --help。", file=sys.stderr)
        return 2
    ForgeApp(workspace, resume).run()
    return 0
