# 时钟/复位域交叉静态分析（阶段 4）

`icforge cdc PROJECT` 对 manifest 声明的 RTL 源做**源码级结构分析**，找出「在一个时钟域进程里被赋值、在另一个时钟域进程里被读取」的信号，写入 `reports/cdc.json`。

它回答一个有哈希证据的问题：**这份 RTL 里有哪些信号跨越了时钟/复位域？** 它**不**回答这些跨越是否安全。

## 为什么需要单独一条路径

主仿真门禁（`icforge verify`）只看功能 PASS/FAIL，结构覆盖门禁（`COV-001`）只看点是否触发。两者都不会指出「`clk_a` 域写的寄存器的值被 `clk_b` 域直接采样」这类**亚稳态风险候选**。阶段 4 补上这条源码级结构分析。

它是**结构启发式**，不是 CDC 形式证明：

- 不推理同步器链是否充分、多周期路径、`set_false_path`/`set_max_delay` 例外；
- 不展开 generate/参数化后的网表，只看源码文本；
- 不区分单比特/多比特总线（多比特跨域更容易出问题，但本分析不做位宽判定）；
- 复位域信息只作**评审提示**：异步复位跨时钟域使用是候选，不是结论。

## 分析方法

对每个 RTL 源文件：

1. 去注释后按 `always`/`initial` 切分为进程；用 `begin`/`end` 嵌套配对定位进程体。
2. 从灵敏度列表提取**时钟域**：`posedge`/`negedge` 上的非复位信号；名字命中 `rst|reset|clr|clear` 的信号归为**复位**，不计入时钟。
3. 在进程体内提取被赋值信号（`<=` / `=`）作为驱动者，其余标识符作为读取者。
4. `assign` 连续赋值不归属任何时钟域，但同样记录驱动与读取。
5. 跨文件聚合后，对每个信号：若其驱动域集合与读取域集合存在**不相交的域**，即记为一条 `crossing`。

记录的 `kind`：

- `cdc`：单驱动、跨时钟域读取；
- `multi_driver`：同一信号被多个域驱动（本身已是可综合性问题候选）。

## 报告与状态

`reports/cdc.json` 记录状态、时钟列表、逐进程域、交叉候选、复位域、输入哈希与 `reason`。状态语义：

| 状态 | 含义 |
|---|---|
| `pass` | 未发现时钟/复位域交叉候选（无时钟进程时也记 `pass`，并在 `reason` 说明「无可跨之域」） |
| `review_required` | 发现交叉候选，需要工程评审（附前若干条信号与方向） |
| `blocked` | manifest 未声明 RTL 源，无法分析 |

补充约定：

- 报告绑定 RTL 源哈希与检查器哈希；改动任一输入，旧报告立即失效。`cdc_valid` 会拒绝输入的 RTL 与报告记录不一致的旧报告。
- `pass` 只说明**当前启发式**未见候选，不等于 CDC 已签核。
- 分析不运行仿真、不调用后端，仅读源码，因此不需要 Verilator/Icarus，任何平台可跑。

## 审查门

`CDC-001`（design，`required: false`）映射该报告：缺报告记 `not_run`，`review_required` 记 `review_required`，输入过期或 `blocked` 记 `blocked`，当前输入上无候选记 `pass`。它是可选门，不阻断 `artifact_ready`，但会把候选显式暴露在评审报告里。

`RTL-002`（design，`review_required`）的 `next_action` 指向本命令。

## 夹具

- `examples/agent_validation/cdc_dual_clk/`：双时钟 `dual_clk.v`，`a_reg` 在 `clk_a` 写、在 `clk_b` 读。本机实测 `review_required`（1 条候选：`a_reg` `clk_a -> clk_b`）。
- `examples/agent_validation/ice40_pwm/`、`sync_fifo/`：单时钟设计，实测 `pass`（无候选）。

agents 只可改 `rtl/`；manifest 由宿主固定。

## 复现命令

```powershell
icforge cdc examples/agent_validation/ice40_pwm
icforge cdc examples/agent_validation/cdc_dual_clk   # 期望 review_required
```

## 边界（未做，不声称）

- 这是**源码结构启发式**，不是 CDC/RDC 形式证明；`pass` 不证明无亚稳态风险。
- 不分析网表级、门级或布局后的 CDC；不做同步器识别与充分性判定。
- 不做多比特总线一致性、复位释放/收敛时机分析。
- 候选是否可接受由工程评审决定；本命令只暴露候选，不给判定。
