# 布线后时序签核与物理签核路径（阶段 6）

`icforge timing PROJECT` 读取布线工具写出的实现报告（`reports/implement.json`），把其中的**静态时序签核**结果汇总到 `reports/timing.json`，并尝试定位一个 **post-route / 门级仿真器**。它补上「综合/布线之后」的时序证据与物理签核路径。

它回答一个有哈希证据的问题：**这份 RTL 在所选后端布线后，关键时钟的静态时序签核是否通过？post-route 仿真是否可运行？** 它**不**伪造缺失的物理验证。

## 为什么需要单独一条路径

实现层（`icforge implement`）在布线后会打印 `Max frequency for clock '<clock>': <MHz> (PASS|FAIL at <MHz>)`。这条信息在 `implement.json` 里，但主交付门（`IMPL-003`/`DELIV-001`）不专门汇总时序语义。阶段 6 把它整理成独立的 `timing` 报告，并在同一份报告里给出 post-route 仿真的**路径状态**，让「时序签核」和「post-route 物理仿真」分开呈现、分别诚实。

## 静态时序签核（真实证据）

- 对 iCE40（Yosys + nextpnr-ice40 + icepack）后端，`implement.json` 的 `timing` 段来自 **nextpnr-ice40 的真实输出**：`clock`、`max_frequency_mhz`、`constraint_mhz`、`pass`。
- `timing_status=pass` 表示该时钟满足约束；`fail` 表示违反。两者都如实记录，不套用任何「估算」标签。
- **这不是安路 STA**，也不是「所有端点都被约束」的声明。开放后端只给出它实际报告的信息；多时钟、多端点、建立/保持裕量的完整分析不在此处。

## post-route 仿真（诚实脚手架）

post-route / 门级 SDF 仿真需要**目标相关**的仿真器：

- iCE40：`icesim`（若可用）；也可用环境变量 `ICFORGE_ICESIM` 指定可执行文件。
- 其他后端族：报告 `reason` 说明「未定义 post-route 仿真器」。

当前环境**没有 `icesim`**，因此 `post_route_status=blocked`，`reason` 写明缺哪个工具、如何指定。整体 `status=blocked`——即使静态时序已 `pass`。这是有意的诚实 blocked，不是失败、也不是可忽略的跳过。

## 报告与状态

`reports/timing.json` 记录状态、`timing_status`、`post_route_status`、目标、时序数据、利用率、post-route 工具状态、输入哈希与 `reason`。状态语义：

| 字段 | 状态 | 含义 |
|---|---|---|
| `timing_status` | `pass`/`fail`/`blocked` | 静态时序签核结论 / 无时序数据 / 实现证据无效 |
| `post_route_status` | `pass`/`blocked` | post-route 仿真是否真的跑过 / 缺工具或未执行 |
| `status` | `pass` | 时序签核 `pass` **且** post-route 仿真 `pass` |
| | `fail` | 静态时序签核 `fail` |
| | `blocked` | 其余情况（缺实现报告、实现无效、无时序数据、post-route 缺工具） |

补充约定：

- 报告绑定 `implement.json` 哈希、`@checker` 哈希与 RTL 源哈希。改动任一输入，旧报告立即失效。
- 缺 `reports/implement.json` 时记 `blocked`，提示先运行 `icforge implement PROJECT`。
- `pass` 只说明**当前后端、当前输入**上的静态时序签核与（若可用）post-route 仿真；不是芯片级物理签核，也不是实板验证。

## 审查门

`TIM-001`（implementation，`required: false`）映射该报告：缺报告记 `not_run`，`fail` 记 `blocked`（附原因），`blocked`（如 post-route 缺仿真器）记 `blocked`，只有完整 `pass` 记 `pass`。它是可选门，不阻断 `artifact_ready`，但会把时序/物理签核的缺口显式暴露在评审报告里。

`DV-004`（verification，`required: false`）继续记录「ASIC 物理签核不在本流程内」，与 `TIM-001` 分开，避免把开放后端的静态时序信息误当成物理签核。

## 夹具

- `examples/agent_validation/ice40_pwm/`：本机实测 `timing_status=pass`（clock `clk$SB_IO_IN_$glb_clk`，max 326.9 MHz，constraint 12.0 MHz），`post_route_status=blocked`（`icesim` 不可得），整体 `status=blocked`。

## 复现命令

```powershell
icforge implement examples/agent_validation/ice40_pwm
icforge timing  examples/agent_validation/ice40_pwm
```

## 边界（未做，不声称）

- 只汇总后端工具**实际报告**的静态时序；不做多时钟/多端点/裕量分析。
- 未运行任何 post-route SDF 仿真（当前无 `icesim`）；`post_route_status=blocked` 如实记录。
- 未做实板验证、未下载到板、未做功耗或信号完整性分析。
- 不是 ASIC 物理签核（`DV-004` 仍为 `not_run`）。
