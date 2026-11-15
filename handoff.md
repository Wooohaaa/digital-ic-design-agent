# IC Forge 工程交接

更新日期：2026-11-14（Asia/Shanghai）。本文件记录本地状态与会话已完成工作。本次新增形式验证/LEC 门禁、厂商中立综合（RTL→netlist）门禁、SystemVerilog 支持夹具、最小 class 化（UVM 风格）验证环境门禁、厂商中立多后端实现层（阶段 2）、RTL 结构覆盖率及交付集成（阶段 3）、CDC/RDC 源码结构分析（阶段 4）、RTL 功能/断言覆盖（阶段 5）、post-route 时序/物理签核路径（阶段 6）与版本化发布聚合（阶段 7），并运行了完整 Python 回归，未启动 agent 任务、调用模型或操作开发板。

## 接手先读

- 工程目录：`C:\Users\chiehyu\Desktop\ICT\icforge`；旧路径 `ICT/fpga-design-agent` 为兼容入口。
- 目标：从需求、RTL 生成与修复，到仿真、lint、安路实现、工程证据审查和交付；支持主动持续任务，以及调度 Codex、Claude Code、OpenCode 分工。
- 用户优先追求少人工干预的复杂任务持续执行，但完成必须由当前工程证据验收。
- **最新 mutation audit、形式验证/LEC、厂商中立综合、SystemVerilog 支持、最小 class 化验证环境、厂商中立多后端实现层（阶段 2）、RTL 结构覆盖率与交付集成（阶段 3）、CDC/RDC 源码结构分析（阶段 4）、RTL 功能/断言覆盖（阶段 5）、post-route 时序/物理签核路径（阶段 6）、版本化发布聚合（阶段 7）、任务指标和三份技能已实现到候选分支；本轮已运行完整 Python 回归（341 passed, 1 skipped），仍未合并主线。**
- 先阅读 `docs/engineering-validation.md` 和 `docs/engineering-validation-current.json`，再看当前工程 `project.json`、`reports/`、任务状态和交付指纹。

## Git 和发布身份

| 项目 | 状态 |
| --- | --- |
| 公开仓库 | `https://github.com/Wooohaaa/digital-ic-design-agent` |
| 本机分支 | `codex/agent-eval-mutation-audit` |
| 本机 HEAD | `b757b0f`：Add mutation audit and agent evaluation metrics |
| 上次发布核实的远端候选提交 | `16077afe8fd7d2b578571a9f518a63d45d13eafe` |
| 本地/远端共同 tree | `b1f8601a234e4714f9e03fb246833c1fdecbd6d2` |
| 上次发布核实的主线基线 | `fa8368a37202cf38ab9211f99edc9b6dfb2f39d3` |
| 包/许可 | `icforge` 0.6.0，MIT；Python 导入路径保留 `fpga_agent` |

上轮普通 `git push` 连接 `github.com:443` 失败，改用 GitHub connector 的 Git Data API 发布，因此两侧 commit SHA 不同，tree 完全一致。本次未刷新远端。继续推送前先读取远端分支头并处理两侧历史，不能仅因内容相同就 force push。

公开候选入口：`https://github.com/Wooohaaa/digital-ic-design-agent/tree/codex/agent-eval-mutation-audit`。上次发布核实时，该提交没有 CI 状态或 workflow run；主线历史全绿 CI 不代表新候选通过。

## 启动与工具链

任意终端目录运行 `icforge` 会以当前目录为默认工作区。接手本工程时先进入正确目录：

```powershell
Set-Location 'C:\Users\chiehyu\Desktop\ICT\icforge'
icforge
```

本次确认全局启动器可被解析为 `C:\Users\chiehyu\.local\bin\icforge.cmd`。若遇到 Python 环境错配，优先运行项目入口，使用现有虚拟环境：

```powershell
.\Start-ICForge.cmd
# 或显式执行
.\.venv\Scripts\python.exe -m fpga_agent --workspace .
```

干净开发环境依赖由 `uv.lock` 固定：

```powershell
uv sync --locked --all-extras
```

Python 最低 3.11；历史本机为 3.11.9。`toolchain.json` 记录 Icarus 12.0 devel、Verilator 5.048、Anlogic TD 6.2.168.116。Icarus 的本地入口是 `.tools/icarus/bin/iverilog.exe`，不保证在 PATH。TD full 为 Windows 本机能力；GitHub runner 不含 TD。原厂资料位于 `C:\Users\chiehyu\Desktop\FPGA\anlogic board docs`，HDMI 对照示例为其下 `lab_hd_1_mipi_hdmi`。

模型经 TUI `/connect` 配置。用户第三方 API 偏好为 **DeepSeek，不选 Gemini**；原生 Codex 多工试验指定过 `gpt-6-luna`。模型 ID/可用性以当前后端实际响应为准，不能把旧 exchange 导入记录当新在线调用。凭据留在环境或本机配置，不复制到 handoff、日志或 GitHub。

## 目录与核心入口

| 路径 | 作用 |
| --- | --- |
| `fpga_agent/cli.py`、`tui.py`、`terminal.py` | CLI/TUI 与工具计划 |
| `fpga_agent/autonomy.py`、`task_planner.py` | 持久任务状态、规划、预算、宿主验收 |
| `fpga_agent/orchestration.py`、`agent_backends.py` | 外部 agent 适配、分工、隔离与调度 |
| `fpga_agent/context_budget.py`、`templates/skills/` | 有界上下文与内置技能 |
| `fpga_agent/mutation.py`、`quality.py` | RTL 变异审计与工程审查门禁 |
| `examples/agent_validation/` | FIFO、SPI、APB 三个固定验证夹具 |
| `benchmarks/rv32_single`、`rv32_multi`、`rv32_pipeline` | 三份 RV32I 设计、报告与交付 |
| `benchmarks/hdmi_cube/`、`tinystories260k/` | 视频与模型实板工程 |
| `.fpga-agent/` | 后台任务、日志、恢复状态、委派与 worktree；不随 Git 提交 |
| `.github/workflows/ci.yml`、`tests/` | CI 和已有回归 |
| `docs/` | 使用、实验与验收边界；`docs/experiment-log.md` 为实验索引 |

## 最新一轮具体变更

1. `icforge mutation-audit PROJECT`：仅对 generic Icarus 项目，以输入/模拟器指纹为基线，在临时副本运行 seed 排名的单运算符变异；保存 `reports/mutation.json` 并支持同配置续跑。原始 RTL 和 testbench 不应被改写。
2. 可选审查门 `DV-005`：审计缺失、过期、幸存、编译无效、超时和无结论结果分别报告；kill rate 是诊断数据，幸存可能是等价变异，不能当签核。
3. `icforge task metrics` 与 TUI `/metrics`：统计宿主验收完成率、步数、耗时、失败工具和委派，向规划器提供有界历史参考；当前不计真实 token/费用，也不自动改策略。
4. 内置 `rtl-mutation-audit`、`agent-evaluation-loop`、`sta-constraint-audit` 技能。说明见 `docs/rtl-mutation-audit.md` 和 `docs/agent-evaluation.md`。

### 本轮新增：形式验证与逻辑等价（LEC）

1. `icforge lec PROJECT`：Yosys 证明 RTL 与技术无关门级网表等价（`--mode synth`，写出 `build/lec/<top>_netlist.v`），或两份 HDL 等价（`--mode designs --gold ... --gate ...`）。报告 `reports/lec.json` 记录逐项 proven/unproven 计数与未证点；部分证明只报 `fail`，不读作等价。
2. `icforge formal PROJECT`：Yosys `sat` 做有界模型检测（`--mode bmc`，`--depth` 以时钟周期计）或时序归纳（`--mode prove`，无界）。默认按 FPGA 约定假设触发器上电归零（`--no-init-zero` 可关闭）；无断言工程报 `not_applicable`。绝不把有界通过写成证明。
3. 可选审查门 `DV-006`（LEC）与 `DV-007`（formal）：通过需当前输入上的完整证明；报告缺失记 `not_run`，反例/不完整证明记 `blocked`。均为 `required: false`，不阻断 `artifact_ready`。
4. 后端为可移植 `yowasp-yosys`（`pip install -e ".[formal]"`）、PATH 上的原生 `yosys` 或 `ICFORGE_YOSYS`；缺少时明确 `blocked`。不使用 SymbiYosys（其 Windows/WASM 运行时会崩溃）。细节见 `docs/formal.md`。

### 本轮新增：厂商中立综合（RTL→netlist）

1. `icforge synth PROJECT`：Yosys 把工程 RTL 降到**技术无关门级网表**，不依赖厂商库/布局布线/许可证，写出 `build/synth/<top>_synth.v`（含 SHA-256）与 `build/synth/<top>_stat.json`，报告 `reports/synth.json` 记录状态、顶层、单元总数、触发器数、逐类型计数、网表路径与哈希、工具版本、输入哈希。
2. 流程为 `read_verilog -sv` → `hierarchy` → `proc/opt/memory/opt` → `chformal -remove` → `flatten` → `techmap/opt` → `tee -o ... stat -json` → `write_verilog -noattr`。`memory` 是必需的（否则 `$mem` 无法被消费），`chformal -remove` 去掉只服务形式验证的 `$check`/`$assume`。**刻意不用 ABC**：可移植 WASM 构建在异步复位设计上不稳定。
3. 可选审查门 `SYNTH-001`：当前输入上综合成功且网表哈希一致记 `pass`，缺报告记 `not_run`，缺 Yosys 记 `blocked`；`required: false`，不阻断 `artifact_ready`。
4. 新模块 `fpga_agent/synth.py` 与共享后端 `fpga_agent/yosys.py`（与 `formal.py` 复用后端解析、脚本/报告落盘、路径校验）。细节见 `docs/synth.md`。
5. 诚实边界：产物是技术无关网表，不是厂商映射/布局布线/位流；`cells`/`flops` 是资源估算，不是面积/时序/功耗，也不是厂商资源报告；改动任一源文件即失效。

### 本轮新增：SystemVerilog 支持与夹具

1. 新增固定夹具 `examples/agent_validation/sv_datapath/`：可综合 SystemVerilog（`always_ff`/`always_comb`、`typedef enum` 状态机 + `unique case`、`typedef struct packed`、参数、`$clog2`、`generate for`/`if`），同一份源码跑通 Icarus 自检仿真、Verilator `-Wall` lint、Yosys 综合（175 单元 / 61 触发器）与 RTL-vs-netlist LEC（70 个等价点全证）。agent 只可改 `rtl/*.sv`；testbench 由宿主固定并 SHA-256 钉在 `project.json`。
2. 新增 `docs/systemverilog.md`：按工具实测的支持矩阵（Icarus `-g2012` / Verilator `--lint-only -Wall` / Yosys `read_verilog -sv`）。已记录的边界：Icarus 不解析 `interface`/`package` 端口，Yosys 不解析 `import pkg::*;`，`assert property` 无可移植路径（RTL 用即时断言 `assert(...)`）。
3. 新增 `tests/test_sv.py`（11 项）：夹具各阶段集成、`sv_props` 模块的 BMC + 归纳证明、以及逐工具能力/边界回归（负向测试会在 Yosys 支持 `import` 后失败，提醒更新矩阵）。
4. `rtl/`、`tb/` 下的 `.v` 与 `.sv` 都会被收集、哈希并纳入证据；CI 的 HDL 夹具作业现覆盖 `sv_datapath`。

### 本轮新增：最小 class 化（UVM 风格）验证环境

1. `icforge svsim PROJECT`：用 Verilator `--binary --timing -Wall` 构建并运行一个**最小、自研的 class 化环境**——随机事务类、绑定 `virtual interface` 的 driver、自检 scoreboard。它不是 UVM 库（无 factory/config_db/sequence/phase/覆盖率）。Icarus 不解析 `class`，所以这条路径只能走 Verilator。
2. 报告 `reports/svsim.json` 记录状态、顶层、源列表、Verilator 版本、C++/make 路径、命令、逐需求 checks、输入哈希与 `reason`。状态：`pass`（构建成功且每需求恰好一条 `CHECK REQ-NNN PASS`，无失败行）、`fail`、`error`（`-Wall` 警告即构建失败）、`blocked`（缺 Verilator/C++/make）。
3. 可选审查门 `DV-008`（verification，`required: false`）：缺报告 `not_run`、工具缺失 `blocked`、失败 `blocked`、当前输入通过 `pass`。
4. 新增固定夹具 `examples/agent_validation/sv_class_env/`：同一份 `rtl/alu8.sv` 既跑 Icarus 自检（`verify`），也跑 `verif/` 下的 class 化环境（`svsim`；本机实测 5/5 需求检查通过，另通过 lint、综合 165 单元/8 触发器、LEC 16 个等价点）。新增 `tests/test_svsim.py`（16 项）。
5. 本机工具链实测：Verilator 构建默认用 `-Os` 编译自身运行库，在 GCC 16.1 / MSYS2 UCRT64 上会因 libstdc++ 移动构造符号链接失败；`-O0/-O1/-O2` 均可，故默认传 `-CFLAGS -O2`。Windows MSYS2 下还需 `VERILATOR_ROOT`（POSIX 形式）与 `MAKE=mingw32-make`，由 `svsim` 自动推导。
6. `docs/svsim.md` 记录用法、状态语义、工具链要求与边界。CI 的 HDL 夹具作业新增 `sv_class_env` 的 verify/lint 与一步 `svsim`。

### 本轮新增：厂商中立多后端实现层（阶段 2）

1. `fpga_agent/targets.py`：数据驱动的目标 profile（`anlogic-hx1p35a` / `lattice-ice40-hx1k` / `generic`），含 `list_targets`/`get_target`/`validate_target`/`resolve_target`。未知目标报错，绝不静默回退到别的厂商；`capabilities` 声明目标能做什么，`generic` 明确没有 place_route/bitstream。
2. `icforge implement PROJECT [--target ...] [--stage ...]` 与 `icforge targets`：统一实现层。按目标 profile 分派到后端，产出统一 `reports/implement.json`（阶段、产物路径/哈希、资源、时序、位流、输入哈希、reason）与 `reports/implement.log`。
3. `fpga_agent/ice40.py`（开放后端）：完整免许可证的 iCE40 流——Yosys `synth_ice40` → `nextpnr-ice40 --hx1k --package tq144` → `icepack`。工具解析支持 `ICFORGE_NEXTPNR`/`ICFORGE_ICEPACK` 覆盖、PATH 原生可执行文件或 portable `yowasp` 入口；缺任一即 `blocked`，绝不伪造位流。解析 nextpnr 的 ICESTORM 资源与 `Max frequency ... (PASS/FAIL at ...)`。
4. `fpga_agent/implement.py`：计划解析、三后端分发、统一报告与 `implement_valid`。`anlogic` 后端是薄适配器——调用已有 `do_td` 再把 `reports/td.json` 映射进统一 schema，保证 TD 逻辑只有一份实现；`generic` 后端复用 `run_synth`，`pass` 只表示综合通过，不声称位流。
5. 新增可选审查门 `IMPL-003`（implementation，`required: false`）：缺报告 `not_run`、工具/约束缺失 `blocked`、失败 `blocked`、当前输入通过 `pass`。不改变 `artifact_ready` 的既有 TD 交付语义。
6. 新增 `pyproject.toml` 的 `openfpga` extra（`yowasp-nextpnr-ice40`，自动带 `yowasp-icepack`/`icepll` 等）并更新 `uv.lock`。
7. 新增固定夹具 `examples/agent_validation/ice40_pwm/`：`rtl/pwm_breath.v`（计数器驱动 PWM 占空比与电平 nibble），本机实测 `verify` 3/3 CHECK、`lint` 通过、`synth` 73 单元/13 触发器、`lec` 13 个等价点全证、`implement` 完成开放流（25 LC、`clk` 时序 PASS、产出 32KB `.bin`）。`constraints/pins.pcf` 为 iCEstick 夹具引脚映射，非已核实的板级电气约束。新增 `tests/test_targets.py`（7 项）、`tests/test_ice40.py`（8 项）、`tests/test_implement.py`（18 项）。
8. `docs/backends.md` 记录目标 profile、后端、状态语义、门禁与边界。CI 的 HDL 夹具作业新增 `ice40_pwm` 的 verify/lint 与一步 `synth`+`implement`。

### 本轮新增：实现层验证闭环与交付集成（阶段 3）

1. `icforge coverage PROJECT`：用 Verilator `--binary --coverage` 构建并运行自检 testbench，解析 SystemC `coverage.dat` 得到逐 RTL 源的 line/branch/toggle/expr **结构**覆盖，写 `reports/coverage.json`。覆盖计划从 manifest 的 `coverage` 块（顶层/源/cflags）解析；testbench 是驱动源，RTL 才是统计对象。
2. 新模块 `fpga_agent/coverage.py`：`resolve_coverage_plan`、`run_coverage`、`_parse_coverage_dat`、`_summarize`、`coverage_valid`。构建不叠加 `-Wall`（testbench 是 Icarus 风格、宿主所有），只抑制实测存在的宽度/声明警告；严格 lint 仍是对 RTL 的独立门禁。`coverage.dat` 由 Verilator 写在项目根 cwd，记录行形如 `C '<key>\x02<value>\x01...`，命中数取 `h` 字段最后一个空格后的整数。
3. 可选审查门 `COV-001`（verification，`required: false`）与 `DELIV-001`（integration，`required: false`）。`COV-001` 把结构覆盖接入；`DELIV-001` 对非安路、有 `bitstream` 能力的目标记厂商中立交付证据。`artifact_ready` 的 `fpga` 范围据此由 `valid_td` **或** `neutral_deliverable` 满足。
4. `do_package` 厂商中立分支：非安路且 `capabilities.bitstream` 时用 `implement_valid` 校验实现证据，打包 `.bin` 并在 `delivery.json` 写 `vendor_neutral_bitstream`；最终重检也走中立路径（实现证据 + 归档位流哈希）。`generic`（仅综合）不声称交付。
5. `evidence.py`：`input_hashes` 容忍缺失 `timing_constraints` 与缺失 `board/hx1p35a.json`，对已有安路工程行为不变。
6. 新固定夹具 `ice40_pwm` 的 `tb_pwm_breath.v` 末尾加复位脉冲以覆盖 `rst_n:1->0` 翻转；`project.json` 的 trusted testbench 哈希更新。本机实测 `coverage` pass 62/62（100%）；`review --scope fpga` 下 `artifact ready: True`，`IMPL-003`/`DELIV-001`/`COV-001` 均 pass；`package` 产出交付 ZIP（含 `.bin`、`reports/coverage.json`、`reports/implement.json`、`MANIFEST.sha256`）。
7. `docs/coverage.md` 记录用法、状态语义、coverage.dat 格式、工具链要求与边界。CI 的 HDL 夹具作业新增一步 `coverage`。
8. 诚实边界：结构覆盖不是功能/断言覆盖；iCE40 布线后仿真（`icesim`）在当前环境不可得（无 `yowasp-icesim` 包），明确未做；位流未下载到板、未做实板验证。

### 本轮新增：CDC/RDC 源码结构分析（阶段 4）

1. `icforge cdc PROJECT`：`fpga_agent/cdc.py` 对 manifest 声明的 RTL 源做**源码级结构分析**——按 `always`/`initial` 切分进程，从灵敏度列表提取时钟域（`posedge`/`negedge` 上非复位信号）与复位域，跨域驱动/读取的信号记为候选，写 `reports/cdc.json`+`.log`。它**不**证明 CDC/RDC 安全性，不展开 generate/网表，不区分位宽，复位域只作评审提示。
2. 记录 `kind`：`cdc`（单驱动跨域读取）与 `multi_driver`（同信号多域驱动）。状态：`pass`（无候选）、`review_required`（有候选，附方向）、`blocked`（无 RTL 源）。
3. 可选审查门 `CDC-001`（design，`required: false`）；`RTL-002` 的 `next_action` 指向本命令。
4. 新固定夹具 `examples/agent_validation/cdc_dual_clk/`（`rtl/dual_clk.v`）：`a_reg` 在 `clk_a` 写、`clk_b` 读，本机实测 `review_required`；`ice40_pwm`/`sync_fifo` 单时钟实测 `pass`。新增 `tests/test_cdc.py`（10 项）。`docs/cdc.md` 记录用法与边界。CI 的 HDL 夹具作业新增一步 `cdc`（`cdc_dual_clk` 用 `|| true` 容忍预期 `review_required`）。

### 本轮新增：RTL 功能/断言覆盖（阶段 5）

1. `icforge coverage PROJECT --functional-coverage`（或 manifest `coverage.functional_coverage: true`）：`coverage.py` 升级到 `COVERAGE_VERSION=2`，在同一 Verilator `--binary --coverage` 管线上额外收集 `cover property` 点（`coverage.dat` 里类型 `user`、对象 `cover|S<line>`），并检测 `assert property` 违例。
2. `_summarize_functional` 按源聚合功能点并列出 `uncovered_points`；`_assertion_failure` 在输出里搜 `assertion failed`（不区分大小写）。任何断言违例即判 `fail` 并记 `assertion_failure`。请求功能覆盖却无 cover 点记 `blocked`（不满足请求），不是裸 `pass`。
3. 关键实测：Verilator 5.048 对时钟边沿上的状态断言**在 NBA 之后采样**（`en |-> cnt != 15` 即使 testbench 在 cnt 到 15 前拉低 en 仍会触发）。功能覆盖夹具因此改用复位行为断言 `!rst_n |-> cnt == 0`，与 enable/count 采样顺序无关。模块级并发断言可用；`always` 块内带时钟事件的进程内断言不支持。
4. 报告新增 `structural_status`/`functional_status`/`functional_requested`/`functional`/`assertion_failure`；整体 `status` 结构优先、再按是否请求功能覆盖综合。配置哈希含 `functional_coverage`，故 `COVERAGE_VERSION=2` 让旧报告自然失效。
5. 可选审查门 `COV-002`（verification，`required: false`）：独立于 `COV-001`，只映射功能覆盖；缺功能段记 `not_run`（请求过则记 `blocked`）、断言违例/覆盖不完整记 `blocked`、完整覆盖记 `pass`。
6. 新固定夹具 `examples/agent_validation/functional_cov/`：`rtl/counter.sv`（4-bit 计数器 + 4 个 `cover property` + 复位断言）、`tb/tb_counter.sv`（复位→计数 40 周期→en=0 保持→复位脉冲）。本机实测：结构 20/20、功能 4/4、`assertion_failure=None`、`status=pass`。`tb` 由宿主固定并 SHA-256 钉在 `project.json`。新增 `tests/test_functional_coverage.py`（16 项）。`docs/coverage.md` 补功能覆盖章节。

### 本轮新增：post-route 时序/物理签核路径（阶段 6）

1. `icforge timing PROJECT`：`fpga_agent/timing.py` 读取 `reports/implement.json`，用 `implement_valid` 校验，提取 `timing` 签核段，写 `reports/timing.json`+`.log`。
2. 静态时序签核是**真实证据**：iCE40 后端的 `timing` 来自 nextpnr-ice40 的 `Max frequency for clock ... (PASS|FAIL at ... MHz)`；`timing_status=pass/fail/blocked` 如实映射。
3. post-route 仿真是**诚实脚手架**：`_resolve_post_route_simulator` 对 iCE40 找 `icesim`（支持 `ICFORGE_ICESIM`），其他族明确无模拟器；当前环境无 `icesim`，`post_route_status=blocked` 并写明原因，整体 `status=blocked`。**不伪造** post-route 仿真结果。
4. 可选审查门 `TIM-001`（implementation，`required: false`）；`DV-004` 继续记录 ASIC 物理签核为未关闭项。新 `tests/test_timing.py`（7 项，含工具解析/运行/门禁/CLI，需拷贝缓存的位流产物）。`docs/timing.md` 记录用法与边界。

### 本轮新增：版本化发布聚合（阶段 7）

1. `icforge release PROJECT [--version VERSION]`：`fpga_agent/release.py` 调用 `review_project(project, "fpga")` 拿全部门禁状态，写 `reports/release.json`（`kind`/`release_version`/`version`/`aggregate`{`artifact_ready`/`engineering_signoff`/`required_open_items`}/`gates`{id→{status,required}}/`inputs`/`reason`/`started_at`）。
2. 聚合**不**放宽门禁：`artifact_ready` 只要求必填门（`valid_sim` + 交付证据），可选门未通过只如实记录；`engineering_signoff` 默认 `not_granted`；`required_open_items` 统计未通过的必填门。
3. `do_package` 增 `version` 参数：产物名加 `-<version>` 后缀，zip 内 `delivery.json` 增 `release_version`；无版本时保持旧行为。`release` 命令在写完清单后调用 `do_package`。
4. `inputs` 由 `input_hashes` 计算，绑定 RTL/testbench/约束/spec；改动输入后旧清单不再描述当前输入。
5. 新 `tests/test_release.py`（7 项：清单/打包/CLI/端到端）。`docs/release.md` 记录聚合语义与边界。CI 的 HDL 夹具作业新增功能覆盖、时序、发布三步。

本轮已运行完整 Python 回归（`uv run --locked --all-extras --python 3.11 pytest ...`）：**341 passed, 1 skipped**，总覆盖率 68%。形式/LEC、综合、SystemVerilog、class 化环境、开放 iCE40 实现、RTL 结构/功能覆盖、CDC、时序与发布聚合夹具均通过；尚未对 CPU/视频工程逐项执行。

## 本轮新增：RSI 路线图 stage 4 tiny GPU（PTX 极小子集）

1. 新固定夹具 `examples/agent_validation/tiny_gpu/`：锁步 SIMT 核心 `tiny_gpu #(LANES=4, NREG=8, IMEM_DEPTH=64, DMEM_BANK_DEPTH=16, DW=32)`，每 lane 私有 8×32 寄存器堆 + 16 字私有 dmem bank，PTX-like 指令子集 `movi/mov/add/sub/mul/mad/ld/st/setp.lt/bra/exit`（`mad` = `rd = ra*rb + rd`，即推理 MAC）。
2. `icforge verify` 通过：`PASS: tiny_gpu runs=7 checks=7`，7 条 `CHECK REQ-NNN PASS`（复位、空闲直到 start、每 lane 点积含 32-bit 回绕 vs 独立参考模型、每 lane 独立 bank 隔离、EXIT 后 done 保持、MOVI/MOV/ADD/SUB/MUL+SETPLT 真路径、BRA 跳过 + SETPLT 假路径）。
3. `icforge lint` 通过（修掉 WIDTHEXPAND 与 reserved `rc` 字段的 UNUSEDSIGNAL）；`icforge synth` 通过（40938 cells / 4978 flops，Yosys 支持 2D `dmem`）；`icforge coverage` 通过（3158/3158 RTL 点、100.0%，TB 增加寄存器/内存/指令 walk 使结构覆盖闭环）；`icforge cdc` 通过（无跨域）。
4. `icforge lec` 在该乘法器密集 netlist 上 300s 超时（WASM Yosys SAT 预算）——真实的缩放限制，**不**为本夹具声明 LEC。
5. 诚实边界：这是最小 inference-only SIMT MAC 引擎，不是 CUDA 兼容 GPU（无 PTX 汇编器、无 CUDA runtime、无 warp 调度、无分支发散、无缓存层次）；只覆盖单层点积类 kernel。
6. 新 `ROADMAP.md`：把 RSI 七阶段逐条映射到 icforge 能力与证据，标注 已实现/部分实现/外部阻塞/未开始；`docs/agent-validation-roadmap.md` 增 tiny_gpu 行与复现命令；CI 的 HDL 夹具作业新增 tiny_gpu 五步门禁。前几轮文档里写的“阶段 4–7”（CDC/功能覆盖/时序/发布）其实是 **stage 1 EDA** 的能力，不是 RSI 路线图的 stage 4–7。

## 本轮新增：RSI 路线图 stage 5 tiny LPU（张量流式处理器）

1. 新固定夹具 `examples/agent_validation/tiny_lpu/`：张量流式处理器 `tiny_lpu #(NMAC=4, NW=16, DW=32)`，片上权重 SRAM（4 bank × 16 字），流式激活输入 `act_valid`/`act_in`，并行 MAC 阵列（每个 `running && act_valid` 沿累加 `acc[g] += wmem[g][idx] * act_in`），共享索引计数器，`idx == NW-1` 时 `done` 断言。`start` 时清零所有累加器（每次流式都是新计算）。
2. `icforge verify` 通过：`PASS: tiny_lpu runs=4 checks=7`，7 条 `CHECK REQ-NNN PASS`（复位、空闲直到 start、每 MAC 点积 vs 独立 32-bit 回绕参考模型、NW 激活后 done 保持、第二次流式运行、act_valid 中途暂停再恢复的 partial stream、复位清除累加器）。
3. `icforge lint` 通过；`icforge synth` 通过（17411 cells / 2186 flops）；`icforge coverage` 通过（720/720 RTL 点、100.0%）；`icforge cdc` 通过（无跨域）。
4. 诚实边界：这是单层 GEMV 引擎，不是商用级 LPU；无稀疏张量、无混合精度、无片上互连、无开源模型端到端推理验证；RSI loop 闭环（阶段 6 的 Agent 自训练模型 + 改进芯片）尚未真正跑通。
5. `ROADMAP.md` 阶段 5 改为 已实现（最小版）；`docs/agent-validation-roadmap.md` 增 tiny_lpu 行与复现命令；CI 增 tiny_lpu 五步门禁。

## 本轮新增：RSI 路线图 stage 6 tiny training GPU + Agent 自训练闭环

1. 新固定夹具 `examples/agent_validation/tiny_train_gpu/`：训练 GPU 的前向计算核心 `tiny_train_gpu #(N=4, DW=32)`，片上 A/B/C 三块 SRAM，host 加载/读取端口，三重嵌套整数 GEMM（`C = ReLU(A*B)`），`raw` 旁路（不应用 ReLU，保留负和），`start` 清零循环计数器/累加器/running/done 并清空 C 内存，`done` 在 `N^3` 周期后断言并保持。
2. `icforge verify` 通过：`PASS: tiny_train_gpu runs=4 checks=7`，7 条 `CHECK REQ-NNN PASS`（复位、空闲直到 start、每个 C 元素 vs 独立 32-bit 回绕参考模型、`N^3` 周期后 done 保持、raw 模式保留负和、第二次运行、复位清空 C 内存与 done）。
3. `icforge lint` 通过；`icforge synth` 通过（6907 cells / 1576 flops）；`icforge coverage` 通过（701/701 RTL 点、100.0%）；`icforge cdc` 通过（无跨域）。
4. 新 `fpga_agent/training.py`：RSI 阶段 6 的软件侧——纯 Python 单层 ReLU 线性模型 SGD 训练（无第三方 ML 框架），合成数据 `Y = ReLU(X * W_true)`；训练后把权重量化为 int32 固定点，用**与 tiny_train_gpu RTL 完全相同的 32 位回绕 GEMM + 符号位 ReLU** 语义跑前向，对比 float 参考模型的正/零模式。默认 seed 下 loss `0.2817 -> 0.0333`（-88.1%），量化 int32 前向 100% 一致，`status=pass`。
5. 新 CLI 命令 `icforge training [project] [--n --samples --steps --lr --seed]`：训练并写 `reports/training.json`。新 `tests/test_training.py`（9 项：forward_int 参考模型/ReLU 钳位/正向保持、SGD loss 下降/确定性/报告落盘/量化、CLI parser 与端到端）。
6. 诚实边界：这是单 tile 整数 GEMM + ReLU 核，不是数据中心级 training GPU（无大规模阵列、高带宽内存、NVLink 类互连、FP8/BF16）；训练是玩具级 SGD 循环，不是 PyTorch 类训练栈；软件训练与硬件前向目前是"语义对齐"，不是板上执行。
7. `ROADMAP.md` 阶段 6 改为 已实现（最小版）；`docs/agent-validation-roadmap.md` 增 tiny_train_gpu 行与复现命令；CI 增 tiny_train_gpu 五步门禁 + training 一步。

## 本轮新增：RSI 路线图 stage 7 tiny SoC（RV32I CPU + DMA 2D 分散/聚集 + 中断双总线主 + GEMM 加速器 + UART）

1. 新固定夹具 `examples/agent_validation/tiny_soc/`：最小 SoC 级整合 `tiny_soc #(N=4, DW=32, BAUD_DIV=8)`，单时钟单复位域。包含：(a) **CPU（总线主 1）**：阶段 2 的 RV32I 核（`rv32_core`）作为总线主，通过 dmem 端口驱动总线；独立 `cpu_rst_n`；调试总线优先；(b) **DMA 引擎（总线主 2，2D 分散/聚集 + 描述符链 + 中断）**：`tiny_dma` 把 N*N 个 32 位字从源 SRAM 窗拷到目的 SRAM 窗（每周期一字），**逐行带可配行步长**（源 `DMA_SRCS`=0x28、目的 `DMA_DSTS`=0x2C，word-indexed；默认 N=线性 memcpy，源步长≠N 聚集、目的步长≠N 分散）；还支持**描述符链**：CPU 在 fabric 的 0x100..0x10F 窗编程 4 槽 × 13 位描述符表（源/目的窗、行步长、`last`、`next`），`DMA_HEAD`=0x38 指定链头、`DMA_CTRL[1]` 开启链模式，引擎沿 `next` 走描述符直到 `last`，整链只中断一次；从口映射到 0x20-0x3F（DMA_SRC=0x20、DMA_DST=0x24、DMA_SRCS=0x28、DMA_DSTS=0x2C、DMA_CTRL=0x30、DMA_STATUS=0x34、DMA_HEAD=0x38），主口读写 fabric SRAM；完成时拉中断 `dma_irq`（顶层输出线）+ `DMA_STATUS[2]` 中断标志，保持到下次 start 清除；fabric 在 GEMM 空闲时对 DMA 写比 host 总线写优先仲裁，DMA 读是独立 mux 故 CPU 可在拷贝中轮询 STATUS；(c) 互连（AXI-like 握手）：host 写/读总线 `bus_we/bus_wstrb[3:0]/bus_addr[8:0]/bus_wdata/bus_rdata` + 地址译码器（A 窗 0x40-0x7F、B 窗 0x80-0xBF、C 窗 0xC0-0xFF、控制/状态寄存器 0x00-0x10、DMA 从口 0x20-0x3F、DMA 描述符窗 0x100-0x10F）；写侧 `bus_wready`=GEMM 空闲，声明操作数 SRAM 写口可用——运行期间向 A/B 窗的原始写被 fabric 丢弃（寄存器写不占用操作数 SRAM，始终接受）；读侧组合读、`bus_rvalid` 恒 1；(d) 计算：整数 GEMM 加速器 `C = ReLU(A*B)`（32 位回绕 + 符号位 ReLU），片上 A/B/C SRAM，`start` 脉冲触发 `N^3` 周期 triple-nested 循环，`done` 断言保持；(e) 串口 I/O：8N1 UART 发送器（`uart_tx`，`BAUD_DIV` 周期/位，LSB-first，start/8 数据/stop），`TXDATA` 触发一帧；(f) IMEM：256 字 word-indexed，TB 加载；(g) 指令缓存（直接映射、阻塞式）：fabric 内 4 行 × 4 字，位于 CPU 取指路径；RV32I 核组合取指、无 stall 输入，故未命中时断言 `imem_stall`、wrapper 经下降沿锁存的门控关 CPU 时钟，refill 期间（每周期一字、4 周期填满一行）下发 NOP（`0x00000013`）；probe 窗 0x014（bit9 flush / bit8 probe_en / bits7:0 idx）控制、0x018 数据读回；tag = `imem_idx[7:4]`（16 字块）区分同线不同块；(h) 数据缓存（直接映射、非阻塞式）：fabric 内 4 行 × 4 字，位于 CPU 数据路径；RV32I 核组合读 dmem、无 stall 输入，故读未命中时同周期**旁路到 SRAM**（CPU 总能读到正确数据）并一次性 refill 整行；一致性用**失效**（无写回）：host/DMA 写 A/B/C 命中字清除该行、GEMM 启动清空整缓存；probe 窗 0x01C（bit10 fill / bit9 flush / bit8 probe_en / bits7:2 addr）控制、0x01D 数据读回；tag = `addr[7:6]`（A=1/B=2/C=3，0=不缓存）、line = `addr[5:4]`、offset = `addr[3:2]`；显式 fill 位是必需的，因为 1 周期 refill 会让 probe 周期内 miss/hit 无法区分；C 窗在 GEMM 空闲时开放 host 写（byte-lane strobe），用于 tag 多样性测试。
2. RTL 结构：`rtl/tiny_soc_core.sv`（被测 fabric：互连译码、寄存器、A/B/C SRAM、GEMM、UART、IMEM、直接映射指令缓存与 DMA 描述符表/接线/仲裁；窄 word-index 接口使每一 bit 可 toggle）+ `rtl/tiny_dma.sv`（被测 DMA 引擎：5 位从口地址 + 3 位从口数据使每个从口 bit 可 toggle；32 位主数据通路由 TB 灌入 LCG 多样数据使其全 bit 可 toggle；row/col 计数器 + 行步长乘法器由 TB 遍历所有步长值 0..7 使其全 bit 可 toggle；描述符链 desc_idx/next/last 由链扫与零化序言遍历使其可 toggle；主地址直接 assign 到输出口）+ `rtl/tiny_soc.sv`（wrapper：例化 RV32I CPU + fabric，持有 32 位 CPU 总线与调试/CPU mux，透出 `dma_irq`；含指令缓存停顿的 CPU 时钟门控）；`rv32_core.v` 是复用 IP，编译但不重复测量（自带 `benchmarks/rv32_single`：114 程序、18,099 退休指令）。
3. `icforge verify` 通过：17 条 `CHECK REQ-001..017 PASS`（复位清 done 且 UART 空闲高、引擎空闲直到 start 脉冲、每个 C 元素 vs 独立 32-bit 回绕参考模型、`N^3` 周期后 done 保持、UART 帧逐周期采样解码、第二次 GEMM 运行、复位清结果与 done、**RV32I CPU 自主运行**、负和 ReLU 钳 0、**DMA 线性 A→B 拷贝逐字比对多样化 32 位源数据 + STATUS/SRC/DST 回读/busy/done**、**DMA 跨窗 C→A 与 B→C + 寄存器窗扫 + 非法窗 src=3 拷贝**、**DMA 2D 分散/聚集（源步长 2、目的步长 4）逐字比对聚集源字**、**DMA 中断（dma_irq + STATUS[2]）完成后断言 / start 清除 / 再完成后重断言**、**DMA 描述符链（desc0 聚集 A→B 并指向 desc1，desc1 线性拷 B→C 且 last；B=聚集源字、C=B、链中 busy、整链只中断一次）**、**AXI-like 握手（wready 空闲高/运行低；运行中向 A[0] 的原始写被丢弃，A[0] 保值；寄存器写 RESULT 不受影响）**、**指令缓存（冷探测触发 refill：imem_stall 断言、refill 期间 0x018 旁路读回 IMEM 原字、完成后返回缓存字；热探测干净命中无 refill；tag 区分同线不同 16 字块）**、**数据缓存（冷探测 miss 同周期旁路返回 SRAM 字；显式 fill 后同行任意 offset 命中；host 写清行后 miss 并旁路到新值；B/C tag 区分同线不同窗口；GEMM 启动清空缓存）**）。
4. `icforge lint` 通过；`icforge synth` 通过（54,977 cells / 11,998 flops）；`icforge coverage` 通过（2616/2616 RTL 点、100.0%，19 个可证不可达点豁免）；`icforge cdc` 通过（无跨域）。
5. 覆盖豁免机制：`fpga_agent/coverage.py` 的 `coverage.unreachable` 声明（`object` + 可选 `line` + `reason`）——匹配点从分母剔除、记录在 `waived_unreachable`、不计为覆盖；未匹配声明记录在 `waiver_unmatched` 防失效；配置 hash 包含豁免声明（改声明即失效重跑）。tiny_soc 豁免 19 个点：2 个 `imem_rd[1:0]:1->0`（RV32I 指令字 bit [1:0] 恒为 `2'b11`）+ 16 个 DMA 主地址低两位（`m_waddr[1:0]`/`m_raddr[1:0]` 及 fabric 侧 `dma_m_waddr[1:0]`/`dma_m_raddr[1:0]`，字对齐拷贝——窗口基地址与行步长都 word-indexed——恒为 0）+ 1 个 `bus_rvalid:1->0`（组合读，读有效线恒 1）。
6. 诚实边界：CPU 是阶段 2 复用 IP；DMA 是**最小 2D 分散/聚集引擎 + 描述符链 + 中断**（固定长度、窗到窗、每周期一字、行步长可配、描述符链可编程、完成拉中断；但无多通道、无 QoS、无跨时钟、与 GEMM 不重叠）；缓存是**最小直接映射**版：指令缓存为阻塞式（4 行 × 4 字、未命中关 CPU 时钟阻塞、无替换策略、无多级），数据缓存为非阻塞式（4 行 × 4 字、未命中同周期旁路 SRAM、写失效一致性、无写回、无预取、无多级）；这是单时钟单域最小 SoC（仅最小 AXI-like 写就绪/读有效握手，无完整 AXI/NoC、MMU、多时钟域、电源管理、SerDes/DDR PHY、编码/解码仅一个 UART 帧）；只有一个 RV32I + DMA + GEMM + UART，不是阶段 7 描述的全场景 SoC 矩阵；`icforge lec` 未运行（乘法器密集 netlist 超时，WASM SAT 预算）。
7. 开发期修复记录（本轮深化）：2D 重写时地址赋值漏了 `8'h40 +` 窗口基址，导致主地址全偏移 −0x40（raddr 打进寄存器区读 0、waddr 打进 A 窗），独立 `tmp/dma_tb2.sv` 定位；行步长回读原截断到 2 位（步长 4..7 读回 0），改为回读全 3 位；TB 里 `32'd(s)` 非法语法改 `32'(s)`。上一轮：`m_raddr` 误声明 input（应 output）；`src_sel`/`dst_sel` 误声明 1-bit；中间基址线常 0 toggle 点内联消除。
8. `ROADMAP.md` 阶段 7 更新（RV32I CPU + DMA 2D 分散/聚集 + 描述符链 + 中断双总线主 + AXI-like 握手总线 + 直接映射指令缓存 + 非阻塞数据缓存、17 条 REQ、2616/2616 + 19 豁免、54,977 cells / 11,998 flops）；`docs/agent-validation-roadmap.md` 增 DMA 描述符链、AXI-like 握手、指令缓存与数据缓存描述；CI 步骤名同步；`docs/engineering-validation-current.json` + `docs/engineering-validation.md` 同步。

## 历史成果与关键边界

- 2026-11-13 基线记录为 245 个 Python 测试通过（1 跳过）、statement coverage 66%；FIFO/SPI/APB 的真实 Icarus 和 Verilator lint 均有通过记录，SystemVerilog 夹具 `sv_datapath` 另通过综合与 LEC，`sv_class_env` 另通过 Verilator `--binary` class 环境。历史 CI 为 `42375073989`，关联主线 `fa8368a`，不是最新候选。
- 三份 RV32I 各有 114 程序、18,099 条退休指令的参考比对与 TD 记录。MIPS 来自仿真周期与 50 MHz 折算，不是板上 CoreMark。单周期核曾 SRAM 下载，但 R11 功能签名未外部采集。
- HDMI 立方体有两次独立 SRAM 重载后 18/18、30/30 视频样本通过记录。默认背景为 RGB(1,1,1)；RGB(0,0,0) 在当前链路失锁的根因仍未查明。见 `docs/hdmi-cube-flow.md`。
- 立方体交付位流为 `benchmarks/hdmi_cube/deliverables/hdmi_cube-hx1p35a.bit`；同目录保留 ZIP、SHA-256、JSON 和实板 MP4。本次只确认文件入口，没有重烧或重验。
- TinyStories260K 有板上模型 CRC、1,536 logits 和生成 token 比对记录，采用 RV32I 软件浮点、串行用户 Flash 和 64-token 上下文。主机负责 tokenizer 与独立校验。见 `docs/tinystories-flow.md`、`docs/tinystories-rounds.md`。
- 工程签核仍为 `not_granted`。CPU STA 覆盖率、官方架构测试/Spike、CDC 形式证明、post-route 仿真、RTL **断言**覆盖率、板卡 revision 引脚复核仍有未决项。通用 RTL 的 formal/LEC、厂商中立综合、class 化验证环境、厂商中立多后端实现、RTL **结构**覆盖（`COV-001`）、**功能**覆盖（`COV-002`）、CDC 源码结构分析（`CDC-001`）、时序/物理签核路径（`TIM-001`）与版本化发布聚合门禁已接入，厂商中立实现也经 `DELIV-001` 接入 `package`/`artifact_ready` 交付路径，但对 CPU/视频工程尚未逐项执行；综合产物为通用门级网表而非厂商映射或位流，iCE40 位流才真正到布局布线/位流但仍未上板，post-route 仿真因缺 `icesim` 如实记 `blocked`。SystemVerilog 支持范围已按工具实测记录（见 `docs/systemverilog.md`）；class 化环境是最小自研集，不是 UVM 库；并发断言只在 Verilator 可用且无断言**覆盖率**（`COV-002` 只测 `cover property` 点命中）。

## 持续任务与实板操作规则

在同一工作区接续任务时先查看现有记录，再决定是否恢复：

```powershell
icforge task list --workspace .
icforge task status --workspace .
icforge task logs --workspace .
icforge task metrics --workspace .
```

`task pause/resume/steer/cancel` 及 TUI `/tasks`、`/pause`、`/continue`、`/steer` 用法见 `docs/autonomous-tasks.md`。预算默认 64 步/24 小时；重启电脑后需显式恢复，当前长期任务跨天远端稳定性尚未验收。一次同工作区只允许一个持久任务修改工程；恢复前确认旧工具子进程已结束。

规格、trusted testbench pin 和验收器属于受保护输入；修复实现时不能为通过门禁削弱它们。委派必须有清晰文件范围，最终由宿主审查集成树和当前指纹。

硬件下载与采集共享板卡独占锁，不能并行重新配置。HDMI 采集需预留接收端锁定时间，默认采集 12 秒并丢弃前 5 秒，检查多个旋转姿态。LED 含义只对实际驱动了相应引脚的诊断位流成立。用户要求通过终端/MCP操作，避免 computer-use。

TinyStories 使用独立 N25Q128 用户 Flash，不能与配置 Flash 混淆；`--upload-model` 有擦写副作用。文档里的 COM5、FTDI 序列号和 5555 服务端口是历史配置，应按当前设备重新检测，5555 不是 UART COM 号。

TUI 风格保留 Hermes 类简洁终端、符号拼成的 ICFORGE 字标、多种文字配色且背景全黑。GitHub 展示使用用户提供的 TUI 图；TinyStories 以文本故事和测量结果展示，用户已要求不放该部分图片。

## 下一轮接手顺序

1. 先验证当前候选的 mutation audit、续跑、证据过期、任务指标聚合及 CLI/TUI 入口，再运行适当完整回归。保持失败反例并更新 `candidate_changes` 的实际结果。
2. 运行五份固定 RTL 夹具（含 `sv_datapath`、`sv_class_env`），分析幸存变异与规格的关系；不能只追求 kill rate。用固定任务集比较模型/编排策略，记录样本数和失败轨迹。
3. 补真实 token/费用、远端长任务稳定性、工程快照回退及有来源/失效条件的工程记忆；逐项验证再扩展自治权限。
4. 继续 CPU 官方架构/参考模拟器、STA 未约束端点、CDC 形式证明和硬件签名闭合；把通用 `icforge lec`/`icforge formal`/`icforge synth`/`icforge svsim`/`icforge coverage`（含 `--functional-coverage`）/`icforge cdc`/`icforge timing`/`icforge release`/`icforge implement` 门禁应用到 CPU/视频工程；下一阶段可补断言**覆盖率**（`cover property` 功能点已由 `COV-002` 提供）与更完整的验证环境，并补齐 post-route SDF 仿真（需 `icesim`；`icforge timing` 已搭好路径并如实记 `blocked`）；ASIC 签核作为另一个能力阶段。
5. 整理有版本的 release 附件：位流、哈希、suite report 和复现说明（`icforge release --version` 已生成版本化清单与 zip）；发布前先确认远端历史与候选回归状态。

可用的回归入口如下（本轮已运行完整回归；下列命令供复现）：

```powershell
uv run --locked --all-extras --python 3.11 pytest --cov=fpga_agent --cov-report=term-missing
uv run python -m fpga_agent verify examples/agent_validation/sync_fifo
uv run python -m fpga_agent lint examples/agent_validation/sync_fifo
uv run python -m fpga_agent lec examples/agent_validation/sync_fifo
uv run python -m fpga_agent formal examples/agent_validation/sync_fifo
uv run python -m fpga_agent synth examples/agent_validation/sync_fifo
uv run python -m fpga_agent verify examples/agent_validation/sv_datapath
uv run python -m fpga_agent lint examples/agent_validation/sv_datapath
uv run python -m fpga_agent synth examples/agent_validation/sv_datapath
uv run python -m fpga_agent lec examples/agent_validation/sv_datapath
uv run python -m fpga_agent verify examples/agent_validation/sv_class_env
uv run python -m fpga_agent svsim examples/agent_validation/sv_class_env
uv run python -m fpga_agent verify examples/agent_validation/ice40_pwm
uv run python -m fpga_agent lint examples/agent_validation/ice40_pwm
uv run python -m fpga_agent synth examples/agent_validation/ice40_pwm
uv run python -m fpga_agent lec examples/agent_validation/ice40_pwm
uv run python -m fpga_agent implement examples/agent_validation/ice40_pwm
uv run python -m fpga_agent coverage examples/agent_validation/ice40_pwm
uv run python -m fpga_agent coverage examples/agent_validation/functional_cov --functional-coverage
uv run python -m fpga_agent cdc examples/agent_validation/ice40_pwm
uv run python -m fpga_agent cdc examples/agent_validation/cdc_dual_clk
uv run python -m fpga_agent timing examples/agent_validation/ice40_pwm
uv run python -m fpga_agent release examples/agent_validation/ice40_pwm --version 0.7.0
uv run python -m fpga_agent mutation-audit examples/agent_validation/sync_fifo
```

本次开始前已有未跟踪 `.icforge-assignment.md`、`Start-ICForge.cmd`、`WORKSPACE-GUIDE.md`、两个 HDMI 诊断工程、`llm/`、`media/` 和 `tmp/`。保留现场；不要在 `.tools/`、`.fpga-agent/trees/`、`td/runs/` 上做无边界搜索或直接递归清理。维护交接时同步更新验证日期、输入指纹、发布位置和剩余门禁。
