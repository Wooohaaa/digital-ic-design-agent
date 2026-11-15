# IC Forge 0.6.0 工程验证状态

更新日期：2026-11-14。本文汇总源码、当前本机工具和已有工程证据；它不把历史试验自动升级为本次回归，也不授予工程签核。

## 软件与工具链

| 项目 | 固定/实测版本 | 当前状态 |
|---|---|---|
| IC Forge | 0.6.0；发行包名已统一为 `icforge`，Python 导入路径仍兼容 `fpga_agent` | 已更新 `pyproject.toml` |
| Python | 最低 3.11；本机 3.11.9；CI 矩阵 3.11/3.12 | 依赖解析锁在 `uv.lock` |
| Icarus Verilog | 本机 12.0 devel；CI 安装 Ubuntu 仓库版本 | 可执行文件在 `.tools/icarus/bin/iverilog.exe`，本机未加入 PATH |
| Verilator | 本机 5.048 / MSYS2 UCRT64 | 已安装；支撑 `lint`、`svsim` 与 `coverage`；历史 `blocked` 报告需通过本次严格 lint 刷新 |
| Anlogic TD | 本机 6.2.168.116 | Windows 本机可用；GitHub runner 不含该厂商工具 |
| Yosys | 本机 0.70（`yowasp-yosys` WASM，`formal` extra） | 支撑 `icforge lec`/`icforge formal`/`icforge synth`/`icforge implement`；缺少时明确 `blocked` |
| YosysHQ nextpnr-ice40 + icepack | 本机 `nextpnr-0.11.1` / `yowasp-nextpnr-ice40`（WASM，`openfpga` extra） | 支撑 `icforge implement --target lattice-ice40-hx1k`（开放 iCE40 布局布线与位流打包）；缺少时明确 `blocked` |

CI 定义于 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)：Python 3.11/3.12 × Windows/Linux 单元回归；独立 Ubuntu 作业安装 Icarus 与 Verilator，并对 FIFO、SPI、APB、SystemVerilog、class 化环境与 iCE40 实现六个固定夹具执行仿真和严格 lint，另跑开放 iCE40 实现、RTL 结构覆盖、CDC 分析、功能覆盖、时序与发布聚合几步。主线提交 `fa8368a37202cf38ab9211f99edc9b6dfb2f39d3` 的最新 GitHub Actions 全部通过：[本次运行](https://github.com/Wooohaaa/digital-ic-design-agent/actions/runs/42375073989)。Anlogic TD 未纳入托管 runner，仍由本机工程门禁负责。

形式验证/LEC、厂商中立综合、SystemVerilog 支持、class 化验证环境、厂商中立多后端实现层、RTL 结构覆盖率、CDC/RDC 分析、功能/断言覆盖、时序/物理签核路径与版本化发布聚合候选改动已在本机运行完整 Python 回归（见下）；HDL 夹具与 TD 仍沿用先前记录。

## 本机回归与覆盖率

2026-11-14 在 Windows / Python 3.11.9 使用已锁定依赖运行完整回归：**341 passed, 1 skipped**，总 statement coverage **68%**（新增 `formal.py`、`synth.py`、`yosys.py`、`svsim.py`、`targets.py`、`ice40.py`、`implement.py`、`coverage.py`、`cdc.py`、`timing.py`、`release.py`、`training.py` 摊薄了历史基数）。关键 agent/工具路径的行覆盖率如下：

| 模块 | 覆盖率 | 说明 |
|---|---:|---|
| `cli.py` | 51% | 新增 CLI/parser 测试；复杂 TD 与硬件分支仍有空白 |
| `formal.py` | 86% | LEC/属性检查、计划解析、证据过期与门禁集成测试；Yosys 缺席分支 |
| `synth.py` | 87% | 厂商中立综合、单元/触发器解析、网表过期与篡改拒绝、门禁集成 |
| `svsim.py` | 76% | Verilator `--binary` 工具链推导、构建/运行状态机、需求校验与门禁集成 |
| `coverage.py` | 81% | Verilator `--coverage` 构建/运行、coverage.dat 解析、结构+功能汇总与门禁集成；缺失工具链分支 |
| `cdc.py` | 87% | 进程切分、时钟/复位域提取、跨域候选聚合、门禁集成；部分注释/边界分支 |
| `timing.py` | 64% | 时序签核汇总、post-route 工具解析、门禁集成；未运行的 post-route 分支 |
| `release.py` | 100% | 聚合发布清单与版本化打包；由 `tests/test_release.py` 覆盖 |
| `targets.py` | 94% | 目标 profile 解析、能力校验、manifest 合并、遗留 Anlogic 描述兼容与未知目标拒绝 |
| `ice40.py` | 79% | 开放 iCE40 后端：工具解析、三段流状态机、资源/时序解析 |
| `implement.py` | 73% | 多后端分发、统一报告、位流校验、门禁集成；anlogic 映射分支 |
| `yosys.py` | 76% | 后端解析、脚本/报告落盘、绝对路径校验；共享管线的错误分支 |
| `autonomy.py` | 75% | 长任务恢复、后台 worker 和覆盖变量隔离已纳入回归 |
| `orchestration.py` | 86% | 调度、隔离 worktree、审查、暂停恢复测试 |
| `task_planner.py` | 86% | 结构化决策、工作区和凭据脱敏测试 |
| `agent_backends.py` | 87% | 由 orchestration adapter 测试覆盖 |
| `ftdi.py` | 84% | Windows CI 通过模拟 D2XX 驱动测试，不依赖接入真实板卡 |
| `bitbang_relay.py` | 84% | 大块双向 socket 传输测试 |
| `hardware_evidence.py` | 73% | CRC、logits、UART 和 checker 指纹失效场景 |
| `cpu_agent.py` | 67% | CPU 候选接口与修复流程测试 |
| `context_budget.py` | 68% | 保留用户约束与超限拒绝测试 |

本机还对 `sync_fifo`、`spi_master`、`apb_pwm_timer` 各执行了真实 Icarus 自检仿真和 Verilator 5.048 `-Wall` lint，六个阶段均通过。`sv_datapath` 这份 SystemVerilog 夹具在同样两步之外，还跑通了 Yosys 厂商中立综合（175 单元 / 61 触发器）与 RTL-vs-netlist LEC（70 个等价点全证）。`sv_class_env` 用同一份 RTL 同时跑通 Icarus 自检与 Verilator `--binary` 的 class 化环境（`icforge svsim`，5/5 需求检查通过），另有 lint、综合（165 单元 / 8 触发器）与 LEC（16 个等价点）。`ice40_pwm` 用开放 iCE40 后端跑通 Yosys `synth_ice40` → nextpnr-ice40 → icepack（`icforge implement --target lattice-ice40-hx1k`，25 LC、`clk` 时序 PASS、产出 32KB `.bin`），另有 Icarus 自检 3/3 CHECK、lint、综合（73 单元 / 13 触发器）、LEC（13 个等价点）与 Verilator 结构覆盖（`icforge coverage`，62/62 RTL 点、100.0%）。阶段 4–7 在本机实测：`cdc_dual_clk` 用 `icforge cdc` 得到 `review_required`（1 条候选 `a_reg` `clk_a -> clk_b`），`ice40_pwm`/`sync_fifo` 单时钟得到 `pass`；`functional_cov` 用 `icforge coverage --functional-coverage` 得到结构 20/20、功能 4/4、无断言违例、`status=pass`；`ice40_pwm` 用 `icforge timing` 得到静态时序签核 `pass`（326.9 MHz max / 12.0 MHz 约束）、post-route `blocked`（无 `icesim`）、整体 `blocked`；`icforge release --version 0.7.0` 生成 `reports/release.json`（`artifact_ready=true`、`engineering_signoff=not_granted`）与版本化交付 zip。此处覆盖率是 Python agent 代码覆盖，不等于 RTL 功能覆盖；尚未达到 100%，也不构成芯片签核。iCE40 位流是真实产物，但未下载到板、未做实板验证。

## 已有硬件/设计证据

- **RV32I**：单周期、多周期、两级流水线均有 114 个程序、18,099 条退休指令的独立参考比对及 TD 实现记录。TD 报告 STA 覆盖率分别为 92.36%、92.24%、92.85%；正 setup/hold WNS 和零 TNS 不代表约束完整。单周期核曾通过 JTAG SRAM 配置，但 R11 串行签名没有外部采集，因此不把仿真分数说成板上测得性能。详见 [CPU 结果](riscv-results.md)。
- **HDMI 立方体**：两次独立 SRAM 重载，采集卡在等待锁定后分别接受 18/18 与 30/30 个视频样本，均识别到多个旋转姿态。setup/hold WNS 为 +5.746/+0.066 ns，TNS 为 0；默认 RGB(1,1,1) 有实板证据。RGB(0,0,0) 仍会在当前链路失锁，根因未证实。详见 [HDMI 记录](hdmi-cube-flow.md)。
- **TinyStories260K**：模型完整性、logits 和生成 token 有板上验收记录；其处理器/固件范围及样本见 [TinyStories 记录](tinystories-flow.md)。

这些证据来自不同日期和不同工程配置，不可合并成一次全仓通过。相关交付当前仍把 `engineering_signoff` 标为 `not_granted`。

## 未关闭事项

1. CPU STA 未覆盖端点、IO delay、时钟/复位例外和板卡 revision 尚未逐项对照官方资料闭合。HDMI 一项的高覆盖率不能替代 CPU 约束审查。
2. 尚未集成 Spike / RISC-V Architectural Certification Tests，也没有 RV32I 专用的形式证明；通用 RTL 的 `icforge lec`/`icforge formal` 已接入为可选门禁（见 [formal.md](formal.md)），但尚未对 CPU/视频工程逐项执行。
3. 厂商中立的 RTL→门级网表综合（`icforge synth`/`SYNTH-001`，见 [synth.md](synth.md)）已落地，但仍是通用门级网表与资源估算；厂商中立的多后端实现层（`icforge implement`/`IMPL-003`，见 [backends.md](backends.md)）已接入 open iCE40 后端，产出真实 `.bin`，并通过 `DELIV-001` 接入 `package`/`artifact_ready` 交付路径（`delivery.json` 记 `vendor_neutral_bitstream`），但该位流未做布线后仿真与实板验证；RTL 结构/功能覆盖（`icforge coverage`/`COV-001`/`COV-002`，见 [coverage.md](coverage.md)）已接入可选门禁，但结构覆盖是 line/branch/toggle/expr，功能覆盖是 `cover property` 点命中，二者都不等于断言覆盖率或规格覆盖；CDC/RDC 只有源码结构启发式（`icforge cdc`/`CDC-001`，见 [cdc.md](cdc.md)），不是 CDC 形式证明；时序/物理签核路径已搭好（`icforge timing`/`TIM-001`，见 [timing.md](timing.md)），但 `icesim` 不可得、post-route 仿真如实记 `blocked`；版本化发布聚合已落地（`icforge release`，见 [release.md](release.md)）。Anlogic TD 映射与厂商位流、CDC 形式证明、post-route 仿真、断言覆盖率与 ASIC 物理签核尚未完成。
4. 普通 RTL 的模型生成 testbench 仍需要独立审阅；generic Icarus 项目现在可用 mutation audit 诊断常见运算符错误的检出能力，但不判定等价 mutant，也不替代规格审阅或 CPU/视频专用验证。
5. HX1P35A 引脚表仍需对照所用板卡 revision 的官方原理图复核。
6. RGB(0,0,0) HDMI 失锁尚无电气或接收端根因结论。
7. Python agent 总覆盖率为 68%（341 passed, 1 skipped）；`cli.py`、`tinyllm.py`、`tinyllm_hardware.py`、`ui_worker.py`、视频捕获及 programmer 的部分错误分支仍需补测。
8. CI 当前已通过 Windows/Linux × Python 3.11/3.12 矩阵及六个 HDL 夹具（含 SystemVerilog、class 化环境与 iCE40 实现）；Anlogic TD full 和板级功能验收仍需在安路本机/实际开发板执行。
9. SystemVerilog 支持范围已按工具实测并记录（见 [systemverilog.md](systemverilog.md)）；class 化环境已接入但只是最小自研集（见 [svsim.md](svsim.md)）；实现后端已多后端化并接入交付路径（见 [backends.md](backends.md)），但只实现 iCE40 一族；RTL 结构/功能覆盖已接入（见 [coverage.md](coverage.md)）；CDC/RDC 只有源码结构启发式（见 [cdc.md](cdc.md)）；时序/物理签核路径与版本化发布已接入（见 [timing.md](timing.md)、[release.md](release.md)）。仍有边界未闭合：Icarus 不解析 `interface`/`package` 端口与 `class`、Yosys 不解析 `import pkg::*;`、无并发断言 `assert property` 的可移植路径（Verilator 5.048 支持模块级并发断言，Icarus 不支持），也没有断言覆盖率（`COV-001`/`COV-002` 分别测结构点与 `cover property` 命中）。

## 复现命令

```powershell
uv sync --locked --all-extras
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
uv run python -m fpga_agent verify examples/agent_validation/tiny_gpu
uv run python -m fpga_agent lint examples/agent_validation/tiny_gpu
uv run python -m fpga_agent synth examples/agent_validation/tiny_gpu
uv run python -m fpga_agent coverage examples/agent_validation/tiny_gpu
uv run python -m fpga_agent cdc examples/agent_validation/tiny_gpu
uv run python -m fpga_agent verify examples/agent_validation/tiny_lpu
uv run python -m fpga_agent lint examples/agent_validation/tiny_lpu
uv run python -m fpga_agent synth examples/agent_validation/tiny_lpu
uv run python -m fpga_agent coverage examples/agent_validation/tiny_lpu
uv run python -m fpga_agent cdc examples/agent_validation/tiny_lpu
uv run python -m fpga_agent verify examples/agent_validation/tiny_train_gpu
uv run python -m fpga_agent lint examples/agent_validation/tiny_train_gpu
uv run python -m fpga_agent synth examples/agent_validation/tiny_train_gpu
uv run python -m fpga_agent coverage examples/agent_validation/tiny_train_gpu
uv run python -m fpga_agent cdc examples/agent_validation/tiny_train_gpu
uv run python -m fpga_agent training
uv run python -m fpga_agent verify examples/agent_validation/tiny_soc
uv run python -m fpga_agent lint examples/agent_validation/tiny_soc
uv run python -m fpga_agent synth examples/agent_validation/tiny_soc
uv run python -m fpga_agent coverage examples/agent_validation/tiny_soc
uv run python -m fpga_agent cdc examples/agent_validation/tiny_soc
```

阶段 7 的 `tiny_soc` 把阶段 2 的 RV32I 核作为总线主、外加一个 DMA 引擎（2D 分散/聚集 + 描述符链 + 中断）作为第二总线主，集成进单时钟域 SoC（AXI-like 握手总线 + 直接映射阻塞式指令缓存 + 直接映射非阻塞式数据缓存 + GEMM 加速器 + UART）；结构覆盖 2616/2616（100%），其中 19 个 toggle 点可证不可达——2 个 `imem_rd[1:0]:1->0`（RV32I 指令字 bit [1:0] 恒为 `2'b11`）、16 个 DMA 主地址低两位（`m_waddr[1:0]`/`m_raddr[1:0]` 及 fabric 侧同名线，字对齐拷贝恒为 0）、1 个 `bus_rvalid:1->0`（组合读，读有效线恒 1）——经 `coverage.unreachable` 显式豁免并记录理由，不计为覆盖（见 [coverage](coverage.md) 与 [ROADMAP](../ROADMAP.md)）。

完整逐项门禁见[工程审查流程](engineering-workflow.md)，本地工具版本见[`toolchain.json`](../toolchain.json)，机器可读摘要见[`engineering-validation-current.json`](engineering-validation-current.json)。
