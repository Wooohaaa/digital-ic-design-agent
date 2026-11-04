# 主动任务与长期执行

IC Forge 的持续任务会循环读取工程证据、决定下一步、执行工具，再依据结果继续规划。目标、工作计划、滚动工作记忆、步骤结果和用户追加要求会保存在 `.fpga-agent/tasks/<ID>/`。

## 在 TUI 使用

连接实时模型后，复杂自然语言设计请求（design、CPU、TinyStories、完整 flow）默认进入持续执行。`/auto off` 可切回单次工具模式，`/auto on` 恢复自动持续执行。明确输入 `/verify` 等已有工具命令仍执行该次工具。

也可以直接创建任务：

```text
/task 为当前工程完成验证、TD 实现和交付，保留失败反例，逐项报告未关闭事项
/task --simulation-only 设计一个带同步复位和溢出保护的计数器，并完成仿真验收
/tasks
/tasks 任务ID
/pause 任务ID
/steer 任务ID 增加复位过程中输入变化的边界覆盖
/continue 任务ID
/continue 任务ID --extra-steps 32 --extra-hours 8
/continue 任务ID --use-current-model
/stop 任务ID
```

在长期任务运行期间直接输入自然语言，会把内容作为追加要求持久保存，在下一次决策前读取。补充要求不会打断当前工具；需要立刻停止时使用 `/pause`。同一工作区只允许一个长期任务操作工程；前台修改工具须等该任务暂停后再执行。

Plan 模式仍需要 `/run` 才会启动。后台 worker 启动后，可以关闭 TUI 或启动它的终端，再从同一工作区用 `icforge` 进入并查看进度。`Esc` 和 `/cancel` 控制前台工具；长期任务使用 `/pause` 或 `/stop`。

## 在 PowerShell 使用

```powershell
$env:OPENAI_API_KEY = '你的 Key'
icforge task start '完成现有 CPU 的验证、实现和交付' --workspace . --project benchmarks/rv32_multi --provider api --model gpt-6-luna --target delivery --max-steps 64 --max-hours 24
icforge task list
icforge task status
icforge task logs
icforge task pause
icforge task steer 任务ID '优先分析仿真失败的第一个反例'
icforge task resume 任务ID
icforge task cancel 任务ID
```

除 `start` 外，省略 ID 时默认操作最新任务；每个子命令均支持 `--workspace 路径`。API Key 通过运行环境传入 worker，任务状态文件不会保存 Key。后台进程结束后恢复任务，需要重新提供环境凭据。支持 `api`、`chat` 和本机 `codex` 后端；`exchange` 保留给单次历史响应导入。

任务保留启动时的模型配置。通过 `/connect` 修正模型或接口后，可用 `/continue ID --use-current-model` 应用当前配置再恢复。显式恢复会开启新一轮失败重试窗口，步数和运行时间预算仍累计计算。

## 执行、验收和恢复

- 每步只能调用已有的具名工程工具，模型输出不能作为 shell 或 Python 代码执行。
- 模型每次获得当前规格、RTL、独立验证/实现证据、工程审查、近期工具退出码和输出。滚动工作记忆保留较早步骤的决策和待办事项。
- `simulation` 目标要求当前工程输入的仿真证据通过；`delivery` 还要求有效 FPGA 实现、当前交付 ZIP、校验文件及包内验证输入/位流指纹一致。
- 多工程任务会保留经工具选择的工程清单，并对清单内每个工程检查目标门槛，最后一个工程通过不足以完成整个任务。
- 模型声明“完成”后，宿主仍检查验收门槛。交付产物通过的范围由工程报告说明，工程签核和实板验收依据各自记录。
- 默认最多 64 次决策、24 小时累计运行时间，等待模型和工具也计入预算。同一证据状态下反复调用相同工具会停止；连续规划错误也有上限。`design` 内部可以调用多次模型，步数预算不等同于 API 请求数或费用预算。
- 暂停/取消会终止正在运行的工具进程树；恢复后读取磁盘证据重新规划。中断工具内部的仿真周期、TD 内部阶段或未完成的模型输出不会原地接续。
- 进程异常退出会释放系统锁，留下的步骤标记为中断。若原工具子进程仍存在，恢复会拒绝重叠执行，并给出 PID，等待原进程结束后再恢复。
- 电脑关机或重启后须手动 `task resume`。电脑休眠期间任务不会运行。耗尽预算的任务需要显式追加步数或时间再恢复。

状态包括 `queued`、`running`、`paused`、`blocked`、`limited`、`interrupted`、`completed`、`cancelled`。`blocked` 会记录具体缺少的输入、工具或能力；处理后可以恢复。

任务目录保留 `task.json`、`events.jsonl`、工具输出和各次规划快照/结果。主状态只保留最近 16 步，完整步骤按目录归档；每秒心跳单独写入小文件。状态通过原子文件替换写入，Windows 文件访问冲突会做短暂重试。输出日志设有大小上限；重新打开 TUI 可看到任务状态变化。

## 本轮验证范围

自动测试覆盖真实后台 CLI 父进程退出、三轮模型决策、实际 Icarus 仿真及完成门槛，也覆盖静默工具超时、暂停后重新规划、追加要求、进程锁异常释放、残留子进程防重入、重复失败停止、旧交付内容拒绝和凭据脱敏。模型服务在这些测试中使用本地固定响应，尚未进行跨天连续运行或远端模型的长时间稳定性测试。

```powershell
python -m unittest discover -s tests -v
```
