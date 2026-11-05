# Agent 编排与长期任务

IC Forge 的 `engineer` 模式调用内置 IC 工程工具；新增 `orchestrate` 模式由主管模型规划工作，指挥本机 Codex、Claude Code、OpenCode 完成分析、实现与审查，再由宿主运行独立验收。两种模式共用持久任务、预算、日志、暂停和恢复机制。

## 在 TUI 使用

在目标 Git 仓库根目录运行 `icforge`，仓库需要至少一条提交。先安装所需 CLI，并分别完成它们的登录。`/agents` 检测可执行程序，安装状态不代表登录或模型服务可用。

```text
/provider codex
/model native
/mode orchestrate
/agents 2 codex
/orchestrate --workspace 实现 FIFO 与仲裁器两个独立模块，分别验证，再进行整体审查
```

这组配置使用已登录的 Codex 作为主管，最多同时运行两个 Codex 执行者。`native` 保留 Codex 本机模型配置；不需要把 ChatGPT 登录转换成 API Key。主管仍以只读方式规划，执行者仅在自己的 worktree 内获得实现权限。

也可通过 `/connect` 给主管配置 API/chat 模型。主管与执行者不必使用同一家模型服务。执行者默认采用各自 CLI 的模型、账号及原生配置；主管的 `OPENAI_API_KEY`、`OPENAI_BASE_URL` 不会自动传给执行者。需要特意覆盖时，在启动 IC Forge 前设置 `ICFORGE_EXECUTOR_OPENAI_API_KEY` 等 `ICFORGE_EXECUTOR_` 环境变量。

```text
/agents 3 codex,claude,opencode
/agent-model opencode opencode/deepseek-v4.1-flash-free
/mode orchestrate
设计目标、验收要求和允许修改的范围……
/agents
/tasks
/pause
/continue
```

启用编排后，普通输入直接作为持久任务目标；运行期间普通输入追加到任务要求。`/ask 问题` 使用原来的对话入口。`/mode engineer` 切回工程模式。`Shift+Tab` 的 Plan/Build 是另一个维度：Plan 先展示启动计划，确认执行后才启动。切换模式和并发设置只影响后续新任务。

`/agent-model 执行者 模型名` 保存当前工作区的执行者模型，`/agent-model claude native` 恢复 CLI 原生配置。`/agents` 同时展示模型配置。TUI 启动的任务及 CLI `task start` 都继承这些默认值，命令行 `--agent-model` 可覆盖。此配置独立于主管的 `/model`，也不会改写 OpenCode 的全局 SenseNova 等现有配置。免费模型名称和可用性会变化，先用 `opencode models opencode` 查询。

未选 IC 工程时默认采用 `workspace` 验收；选中 IC 工程时默认要求完整工程交付，可用 `/orchestrate --simulation-only 目标` 只验收仿真。`--workspace` 显式选择通用工作区。IC 目标仍须通过原有仿真/实现/交付证据门禁。

## 固定宿主验收

通用任务应在启动前创建 `.icforge/checks.json`；TUI 自动读取。检查命令由操作者预先声明，主管不能用自称成功代替检查，也不能在任务过程中替换验收配置。没有配置文件但存在 `tests/` 时，默认运行 Python unittest；其它工程必须显式提供检查。

```json
[
  {
    "name": "regression",
    "argv": ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
    "timeout_seconds": 1800
  }
]
```

`argv` 直接执行，不经 shell。`python`、`python3`、`{python}` 自动使用当前 IC Forge 的解释器。Node 等项目请填写相应可执行程序与参数；Windows 的 `.cmd/.bat/.ps1` 包装器不能直接作为检查程序。检查前后的源码指纹必须一致，之后源码改变会使验收失效，需要重新验证。

## 终端命令

```powershell
icforge task start '实现模块、审查并完成回归' --mode orchestrate --provider codex --model native --target workspace --agents codex --max-agents 2 --checks-file .icforge/checks.json --max-steps 128 --max-hours 24
icforge task agents
icforge task status
icforge task logs
icforge task pause
icforge task resume
```

`--agents codex,claude,opencode` 限定可用执行者，`--max-agents 1..8` 限定实际并发。每批最多八个子任务，可跨多批推进。用重复的 `--agent-model codex=模型名`、`--agent-model opencode=提供商/模型名` 显式指定执行者模型；省略则使用原生 CLI 默认值。`--agent-timeout` 为每个子任务的秒数上限，默认 3600。总任务默认 64 次决策、24 小时，可配置；耗尽后停止，恢复需按主动任务指南显式增加预算。

## 协调与恢复

主管根据复杂度和依赖生成分工，而不是每次固定启动多个进程。每个分工声明 ID、执行者、角色、复杂度、可修改文件范围与前置任务。独立实现可并发；文件范围重叠自动添加串行依赖；审查须依赖本批实现。`auto` 选择已启用且安装的执行者，当前按 Codex、Claude、OpenCode 的固定优先级选择；这里尚无基于成本或历史成功率的动态评分器。

每批创建集成 worktree，各分工拥有独立 worktree。已有未提交源码作为起点快照，主工作区的暂存区不被用于内部提交。无关的未跟踪顶层目录不自动复制。分析/审查角色只读，越范围修改、实现未产生变更、逻辑错误、进程失败或超时都会使分工失败。依赖任务能看到已集成的前置变更和报告。

主管读取报告和宿主检查结果继续修复；失败批次须原地重试，保留已完成节点。每个节点最多三次真实失败；暂停/恢复不消耗失败额度，执行次数与失败次数分别记录。可调整未完成节点的说明或执行者。任务、原生 session ID、日志、补丁和指纹持续落盘。`/pause` 终止活动进程并保存状态；`/continue` 恢复未完成批次，已完成节点不重新运行。关闭 TUI 后后台 worker 可继续；系统关机后需重新启动并恢复任务。不是无限预算运行。

0.6.0 改为按依赖就绪持续补位，单个慢任务不会阻止已满足依赖的其它任务使用空闲槽位。`scheduling` 保存调度策略、并发峰值和派发顺序，任务事件记录派发与结束。等待分工在启动前读取最新补充要求。Windows 下连续出现三次已识别的补丁套壳错误时，会保存工作区、提示使用可靠的 UTF-8 文件写法并恢复原会话；此类工具纠错最多两次，越范围修改与其它失败不能绕过门禁。

子任务工作树统一位于 `trees/<ID>/<batch>/workers/<job>/`，与 `integration/` 集成树分离，允许任务本身叫 `integration`。原生 JSON 事件使用独立的有界长行预算，避免大段补丁事件被普通终端的长行截断规则破坏。显式指定 Codex 模型也保留操作者的原生 provider/认证配置，同时仍以只读方式运行主管，不因模型选择而切换认证通道。

导入主工作区前检查相关文件是否被用户改动；不一致时拒绝覆盖，并保留候选结果。导入日志可处理应用补丁后尚未来得及更新状态的中断。存在孤立工具进程时禁止启动第二个任务，避免重叠写入。

任务数据位于 `.fpga-agent/tasks/<ID>/`，工作树位于 `.fpga-agent/trees/<ID>/`。当前保留这些证据以供诊断，没有自动清理工作树的功能。各 CLI 权限能力不同：Codex 使用自身 sandbox；Claude 限制工具与 MCP；OpenCode 限制工具权限。文件范围检查是导入门禁，不能等同于通用于所有 CLI 的操作系统级隔离。

## 接口依据与验证范围

适配依据：[Codex 非交互运行](https://developers.openai.com/codex/noninteractive/)、[Codex CLI 参数](https://developers.openai.com/codex/cli/reference/)、[Claude Code CLI](https://code.claude.com/docs/en/cli-reference)、[OpenCode CLI](https://opencode.ai/docs/cli/) 与 [OpenCode 权限](https://opencode.ai/docs/permissions/)。

自动回归包含真实 Git worktree 与受控子进程，覆盖并发、依赖、越范围补丁拒绝、逻辑失败、超时、暂停/恢复、用户修改保护、导入恢复、固定验收及指纹过期。受控执行者用于验证协调机制，不能代替真实服务验证。

下面记录初版 Codex 测试；新增 OpenCode / Claude Code 联动的配置及实测见 [多 CLI 联动](backend-linkage.md)。

### 2026-11-05 真实 Codex 验收

使用本机 Codex CLI 0.153.4 与已登录的 ChatGPT 账号，主管真实规划并启动两个外部 Codex 实现进程，以及一个依赖两者的只读 Codex 审查进程。测试工程预先固定 `calc/__init__.py` 与 `tests/test_pair.py`，实现者只能分别新增加法、乘法模块。

| 分工 | 开始时间（UTC） | 结束时间（UTC） | 结果 |
| --- | --- | --- | --- |
| implement-add | 03:46:59 | 03:48:08 | 完成，仅修改 calc/add.py |
| implement-multiply | 03:46:59 | 03:49:14 | 完成，仅修改 calc/multiply.py |
| review-arithmetic | 03:49:15 | 03:50:09 | 只读审查，无发现 |

两个实现的运行时间重叠，审查在两者结束后才开始。总任务约 357 秒、6 次规划决策，最终 `completed`；宿主固定 unittest 的两项测试通过，覆盖整数、负数、零和浮点数。另行重跑同一固定测试也通过，原始契约文件与 Git 中的内容一致。机器可读记录见 [真实运行结果](orchestration-live-result.json)。该小型算术工程用于验证协调链路，尚不能证明复杂 IC 项目或数日连续运行的可靠性。
