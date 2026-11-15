# SystemVerilog 支持范围

IC Forge 的每条厂商中立检查链都直接使用成熟开源工具解析 SystemVerilog，没有自研解析器：

| 阶段 | 命令 | SystemVerilog 入口 |
|---|---|---|
| 仿真 | `icforge verify` | `iverilog -g2012 -grelative-include -I <project> -Wall -s <tb>` |
| 严格 lint | `icforge lint` | `verilator --lint-only -Wall --top-module <top> -I<project> <rtl>` |
| 综合 | `icforge synth` | Yosys `read_verilog -sv` |
| 等价/属性 | `icforge lec` / `icforge formal` | Yosys `read_verilog -sv` / `read_verilog -formal -sv` |

`rtl/` 与 `tb/` 下的 `.v` 和 `.sv` 文件都会被工程收集、哈希并纳入证据；改动任一源文件都会使既有证据失效。

## 实测支持矩阵

下表用固定夹具在本机实测（Icarus 12.0 devel、Verilator 5.048、Yosys 0.70/yowasp）。三列互相独立：某工具不支持某构造不代表流程不支持——但**仿真只能在 Icarus 能解析的子集上运行**，所以 `verify` 的门槛由 Icarus 决定。

| 构造 | Icarus 仿真 | Verilator lint | Yosys 综合/LEC |
|---|:--:|:--:|:--:|
| `always_ff` / `always_comb` | ✅ | ✅ | ✅ |
| `logic`、ANSI 风格端口 | ✅ | ✅ | ✅ |
| `typedef enum` + `unique case` | ✅ | ✅ | ✅ |
| `typedef struct packed`（逐字段赋值） | ✅ | ✅ | ✅ |
| `typedef struct packed` 赋值模式 `'{...}` | ❌ | ✅ | ❌ |
| 参数、`$clog2` | ✅ | ✅ | ✅ |
| `generate for` / `generate if` | ✅ | ✅ | ✅ |
| `package` 定义 | ✅ | ✅ | ✅ |
| 限定类型 `pkg::type`（含端口） | ❌ | ✅ | ✅ |
| `import pkg::*;` | ✅ | ✅ | ❌ |
| `interface` 定义 | ✅ | ✅ | ✅ |
| `interface` 作为端口（含 `modport`） | ❌ | ✅ | ✅ |
| 即时断言 `assert(...)` | ✅ | ✅ | ✅（形式） |
| 并发断言 `assert property` | ❌ | ✅ | 部分 |
| `class`、`virtual interface`、`randomize()` | ❌ | ✅（`--binary`） | ❌ 不可综合 |

要点：

- **Icarus 是仿真侧最弱的一环。** 它不解析 `interface` 端口，也不能把 `package` 里的宽度参数绑定到端口位宽。要同时能仿真，`sv_datapath` 夹具因此避开了 interface 与 package 端口，只保留 Icarus 也能编译的 SV 子集。
- **class 化验证只能走 Verilator。** Icarus 不解析 `class`，所以 UVM 风格的随机环境用 `icforge svsim`（Verilator `--binary`）运行；Yosys 不综合 class。见 [最小 class 化验证环境](svsim.md)。
- **Yosys 不能解析 `import pkg::*;`**（`unexpected TOK_IMPORT`），但支持限定形式 `pkg::type`。综合 `import` 的工程会得到 `status: error`。
- **并发断言 `assert property`** 不是可移植子集：Icarus `-g2012` 不解析它。RTL 里请用即时断言 `assert(...)`；`icforge formal` 会把它们编译成形式属性（见下）。
- 上面的 ❌ 是**当前工具版本的实测边界**，不是设计缺陷；工具升级后矩阵可能变化。

## 形式属性与 SystemVerilog

`icforge formal` 用 Yosys `read_verilog -formal -sv` 读取 RTL 内的即时断言/cover。声明了断言的 SV 设计可做有界（BMC）或时序归纳（无界）检查：

- 没有断言 → `not_applicable`，绝不报 `pass`。
- 反例 → `fail`，`reports/formal.log` 记录反例轨迹。

`examples/agent_validation/sv_datapath` 刻意不内嵌断言，保留为快速、确定性的仿真/lint/综合/等价夹具（本机实测 `verify`/`lint`/`synth`/`lec` 全通过，LEC 70 个等价点全证）。断言行为由 `tests/test_sv.py` 中一个独立的 `sv_props` 模块覆盖：BMC 与归纳都在约 1 秒内证明。

> 经验：把断言放进一个 61 触发器的大设计后再做 `--mode prove`，时序归纳会显著变慢甚至超时（本机实测 >300 s）。形式属性优先放在小而聚焦的模块上，或用 `--mode bmc --depth` 控制代价。

## 夹具

`examples/agent_validation/sv_datapath/` 是一份固定、自检的 SystemVerilog 设计，用于钉住上述子集：

- `rtl/sv_datapath.sv`：`always_ff`/`always_comb`、`typedef enum` 状态机 + `unique case`、`typedef struct packed` 存数、参数化、`$clog2`、`generate for`/`if`。
- `tb/tb_sv_datapath.sv`：主机编写的自检计分板（SHA-256 钉在 `project.json`），打印 `CHECK REQ-xxx PASS` 与 `PASS`。
- 与其它夹具一致：agent 只可改 `rtl/*.sv`；规格、testbench 与清单由宿主固定。

`examples/agent_validation/sv_class_env/` 另外钉住 class 化环境子集：同一份 `rtl/alu8.sv` 既跑 Icarus 自检，也跑 `verif/` 下的 class 化环境（`icforge svsim`），见 [svsim.md](svsim.md)。

## 复现命令

```powershell
icforge verify examples/agent_validation/sv_datapath
icforge lint   examples/agent_validation/sv_datapath
icforge synth  examples/agent_validation/sv_datapath
icforge lec    examples/agent_validation/sv_datapath
icforge svsim  examples/agent_validation/sv_class_env
```
