# IC Forge 终端工作台

复杂设计请求默认启动后台持续任务，`/task`、`/tasks`、`/pause`、`/continue` 和 `/stop` 的用法见 [主动任务与长期执行](autonomous-tasks.md)。

## 启动

在 PowerShell、Windows Terminal 或 VS Code 终端里，进入工程仓库运行：

```powershell
python -m pip install -e .
ic-forge
# 等价入口
fpga-agent
python -m fpga_agent tui
```

选择其它工作区或继续上次对话：

```powershell
ic-forge --workspace "D:\IC Projects"
ic-forge --continue
ic-forge --resume <session-id>
```

启动页使用终端字符拼成 ICFORGE 字标，搭配可选配色。下方边框卡片左侧是字符芯片图案与模型/路径/会话信息，右侧列出实际工具、设计工作流和本机工具链状态。模型状态和输入行位于底部；首次发言后，启动卡片保留在对话滚动区上方。输入 `/theme` 选择颜色主题，输入 `/logo` 选择字符字标；两项选择分别保存到当前工作区。

主题只改变文字、字标、光标和线框颜色；终端内容区域始终保持纯黑背景。

建议使用 110 × 36 或更大的窗口显示完整卡片；80 × 24 使用紧凑工具列表，更窄时改用五行字符字标，始终保留符号拼字和输入区。尊重终端 `NO_COLOR` 设置；如终端被配置为无色模式，可在自己的 PowerShell 中删除该环境变量后重开。无需特殊字体、Nerd Font 或图片资源；Windows Terminal 默认等宽字体即可。

## 第一次使用

1. `/project` 选择已有工程，或 `/init counter-demo` 创建工程。
2. 已有工程可以直接 `/verify`。它会执行真实 Icarus 仿真，按 Ctrl+J 展开逐行日志。
3. `/connect` 选择模型接口并填写模型名。API Key 输入框为密码模式；Key 只进入当前进程内存和任务子进程环境，不写配置、会话或模型请求文件。
4. 直接描述需求，如“设计一个带使能、同步复位、溢出检测的 32 位计数器，生成自检仿真”。模型可以回答设计问题，或选择受支持的设计工具。
5. 需要 FPGA 完整交付时使用 `/flow`，或写明生成、仿真、TD 实现和交付的要求。

底部状态行的空心圆表示未配置模型，实心圆表示配置已就绪；实际模型权限和网络状态在请求时验证。当前没有为用户自动配置 API Key。`exchange` 是已有响应重放，不能用于新对话；已有 CPU 用 `/replay --full` 可以重现，无需在线模型。

## 熟悉的交互

| 操作 | 快捷键 / 命令 |
| --- | --- |
| 发送；多行输入 | Enter；Shift+Enter / Alt+Enter（也支持 Ctrl+Enter 发送） |
| 命令面板 / 搜索 | Ctrl+P；输入 `/` 看建议，Tab 补全 |
| 文件搜索 / 附加文件 | Ctrl+O；输入 `@` 后按 Tab；或直接 `@rtl/core.v` |
| 输入历史 | 单行输入时 ↑ / ↓；多行时保留光标移动 |
| Plan / Build | Shift+Tab 或 `/plan` |
| 执行计划 | `/run` |
| 项目侧栏 / 工具日志 | Ctrl+B / Ctrl+J |
| 取消任务 | Esc 或 Ctrl+C；会终止任务进程及其子进程 |
| 清空输入 | 空闲时 Ctrl+C |
| 新会话 / 恢复 | Ctrl+N 或 `/new`；`/resume` 或 `/resume id` |
| 退出 | Ctrl+Q 或 `/quit` |

Build 模式会执行用户请求对应的受支持工具。Plan 模式保留候选操作，输入 `/run` 后才执行设计工具。文件查看、状态查询等读取操作可直接使用。一次只运行一个任务；运行时可取消、查看帮助和状态，不会同时改写同一工程。

会话自动保存在当前工作区 `.fpga-agent/sessions/`；设置在 `.fpga-agent/config.json`。`/export` 导出 Markdown 会话，`/clear` 清空当前会话。该内部目录已加入仓库忽略规则。会话保留最多 100 条消息，模型使用最近 16 条以及当前工程上下文；没有自动压缩超长对话。

## 从其它 agent 迁移

熟悉 Claude Code、OpenCode、Hermes 等终端 agent 的用户，可以沿用“自然语言输入 + 斜杠命令 + 文件引用 + Plan/Build”的操作方式。已有 `AGENTS.md`、`CLAUDE.md`、`GEMINI.md` 会在工作区和当前工程中读取，作为模型的项目上下文，不会执行其中的 shell 代码。

沿用已有的 `OPENAI_API_KEY` / `OPENAI_BASE_URL` 环境变量即可接入 OpenAI Responses 接口；兼容 Chat Completions 的部署在 `/connect` 中选择 `chat`。所选接口需要支持 agent 使用的结构化 JSON 输出。对于只提供 `/v1/chat/completions` 的服务，必须选 `chat`，否则 `api` 会错误请求 `/v1/responses`。IC Forge 会发送自己的 User-Agent，避免部分 Cloudflare 网关将 Python 默认客户端识别为浏览器签名并返回 403 / 1010。`codex` 会使用本机已安装 Codex CLI 的登录状态；模型名必须是该账户实际支持的型号，本机此前对 gpt-6-luna 的 CLI 调用返回 model_not_found。不要把 CLI 登录支持和 API 模型权限混为一谈。

不会自动读取其它 agent 的私有凭据或会话数据库；本版不提供 Claude、Hermes、OpenCode 私有会话格式的导入，也没有通用终端 shell / MCP 工具执行器。模型能调用的操作被限定在下述 IC 流程中，返回的文本不会当作 shell 或 Tcl 执行。

## 配色与字标

当前提供 Forge Gold、Rose Milk、Citrine Grove、Nord Frost、Tokyo Night、Catppuccin Mocha、Catppuccin Latte、Carbon Mint、Cobalt Signal 九套主题。Rose Milk 与 Citrine Grove 取色自用户给出的参考图；Nord、Tokyo Night、Catppuccin 主题沿用各项目公开调色板的色彩关系，并针对终端可读性做了界面映射。更多说明见 [TUI 主题与字标](tui-themes.md)。

ICFORGE 字标可选 Block Forge、Isometric、Pixel Glitch 和 Minimal 四种；均由终端字符绘制，不依赖图片、额外字体或联网资源。

## 常用流程

```text
/init counter-demo
/design --full 设计一个 32 位计数器，带同步复位、计数使能和溢出标记，生成覆盖边界情况的自检 testbench
```

也可以先生成并仿真，检查代码后再实现：

```text
/design 设计一个可参数化的计数器并仿真验证
/files
/open rtl/hx1p35a_heartbeat.v
/diff
/synth
/td
/package
```

`/files` 中显示实际生成的文件名；上面的 RTL 文件名只用于说明模板入口。模型产生的新顶层 IO 若没有已核实的板卡引脚，完整 TD 实现会被门禁拦截，需先补充板卡映射。

RISC-V 专用独立验证流程：

```text
/cpu my-cpu multi --full
/project benchmarks/rv32_pipeline
/verify
/replay --full
/suite
```

架构可选 single / multi / pipeline。CPU 工具当前使用固定 RV32I 执行契约；扩展指令集、特权、中断等需要扩展实现和独立验证 profile，单靠聊天需求不会自动增加这些能力。普通模块的 testbench 由模型生成，验证强度与 CPU 的固定参考模型不同。

## 状态和产物

右侧 SIM / TD 以当前文件和可信参考的指纹检查显示 CURRENT、STALE、FAILED 或 NOT RUN，旧报告不会自动当作当前通过。时序和资源值是最近记录的数据，是否有效以 CURRENT 状态为准。

`/status` 查看原因，`/artifacts` 列出报告、ZIP 和位流。完整交付仍要求当前仿真通过、TD 时序收敛和 bitstream 指纹一致；`/package --simulation-only` 显式生成仿真材料。板卡下载由用户在 TD BitWriter 中完成，本版没有自动上板功能。

长任务使用独立 Python/EDA 进程，工具输出实时进入日志；模型回答当前在完整结构化响应返回后显示，不提供模型 token 流式展示。退出或取消会清理进程树。任务日志保存在 `.fpga-agent/jobs/`，最多保留每次任务 4 MB 的 UI 日志；可自行删除该目录回收空间。

## 开发和检查

```powershell
python -m unittest discover -s tests -v
python -m pip wheel . --no-deps --wheel-dir dist
```

TUI 测试用 Textual Pilot 驱动真实键盘、弹窗和布局，并用本地 HTTP 测试服务覆盖“模型接口 → 工具计划 → Icarus 仿真”；测试服务不代表真实 Luna 调用。截图脚本使用可选开发依赖 `resvg-py`：

```powershell
python -m pip install resvg-py
python scripts/capture_tui.py --verify
```

框架参考：[Textual workers](https://textual.textualize.io/guide/workers/) 与 [Textual testing](https://textual.textualize.io/guide/testing/)。
