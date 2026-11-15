# RSI-Chip 7-Stage Roadmap → icforge 映射

> 本文档把党哥的 RSI-Chip 七阶段路线（[rsi-labs/rsi-chip](https://github.com/rsi-labs/rsi-chip)）
> 逐条映射到 `icforge` 项目的实际能力与证据上。每个阶段标注：**已实现 / 部分实现 / 外部阻塞 / 未开始**，
> 并附可复现的命令与结果。诚实契约：有界证明不是无界证明，受阻不是通过，部分等价不是等价，
> 未运行的验证不算通过，结构性覆盖不等于功能覆盖，静态分析候选不是最终裁决。

---

## 阶段 1：Agent-driven EDA 全栈（SystemVerilog + 最小 UVM + 严格 LEC + RTL→netlist）

**目标**：AI Agent 逐步设计出 SystemVerilog 完备、UVM 最小子集完备、完成严格 LEC、full stack RTL→netlist 的 EDA，初步达到 Cadence/Synopsys 的能力。

**icforge 状态：部分实现** — 核心 EDA 流水线已存在并通过本地验证，但**不是**商用级 EDA。

| 能力 | icforge 工具 | 证据 | 状态 |
|---|---|---|---|
| 仿真（Icarus 12，SystemVerilog-2012） | `icforge verify` | 所有 fixture 的 `CHECK REQ-NNN PASS` + `PASS:` 行 | ✅ 已实现 |
| lint（Verilator 5.048 `-Wall`，WIDTHEXPAND/UNUSEDSIGNAL 为错误） | `icforge lint` | 各 fixture `lint: pass` | ✅ 已实现 |
| 综合（Yosys WASM，generic netlist） | `icforge synth` | `synth.json` cells/flops 统计 | ✅ 已实现 |
| 逻辑等价（LEC，RTL vs synth netlist） | `icforge lec` | `lec.json` proven/unproven（小 fixture 通过；tiny_gpu 超时） | ⚠️ 受限 |
| 结构覆盖（Verilator `--coverage` line/branch/toggle/expr） | `icforge coverage` | `coverage.json` 100% 点位 | ✅ 已实现 |
| 功能覆盖（`cover property` + 并发断言） | `icforge coverage --functional-coverage` | functional_cov fixture 20/20 结构 + 4/4 功能 | ✅ 已实现 |
| CDC（源级时钟/复位域交叉启发式） | `icforge cdc` | cdc_dual_clk fixture `review_required` | ✅ 已实现（非证明） |
| 后布局时序（nextpnr-ice40 静态时序 + icesim 脚手架） | `icforge timing` | ice40_pwm 时序 signoff pass；post_route blocked | ⚠️ 受限 |
| 版本化发布（聚合所有门禁） | `icforge release` | `release.json` + delivery.json | ✅ 已实现 |
| 最小 UVM 子集（事务/驱动/记分板，Verilator `--binary`） | `icforge svsim` | sv_class_env fixture | ✅ 已实现（自编最小环境，非 UVM 库） |
| SystemVerilog 子集（always_ff/comb、typedef enum/struct、`$clog2`、generate） | `icforge verify/synth/lec` | sv_datapath fixture | ✅ 已实现 |
| 形式验证（BMC/归纳，Yosys SAT） | `icforge formal` | formal.py + 小 fixture | ⚠️ 受限（WASM SAT 预算） |

**诚实限制**：
- `icforge lec` 在乘法器密集的 netlist（如 tiny_gpu，4 万 cell）上 300s 超时 — WASM Yosys SAT 预算的真实缩放限制。
- `icforge formal` 同样受 WASM SAT 预算限制，只对小模块有效。
- `icforge cdc` 是源级结构启发式，**不证明** CDC 安全性，不展开 generate/netlist。
- 结构覆盖**不等于**功能覆盖或断言覆盖。
- 这套 EDA 是**开源工具链的封装 + 统一门禁**，不是 Cadence/Synopsys 级别的商用 EDA。它证明了 Agent-driven EDA 的**可行性**，不是**完备性**。

**复现**：
```powershell
# 各 fixture 的门禁
.venv\Scripts\python.exe -m fpga_agent.cli verify examples/agent_validation/<fixture>
.venv\Scripts\python.exe -m fpga_agent.cli lint examples/agent_validation/<fixture>
.venv\Scripts\python.exe -m fpga_agent.cli synth examples/agent_validation/<fixture>
.venv\Scripts\python.exe -m fpga_agent.cli coverage examples/agent_validation/<fixture>
.venv\Scripts\python.exe -m fpga_agent.cli cdc examples/agent_validation/<fixture>
```

---

## 阶段 2：RV32I CPU — 验证 EDA 功能

**目标**：AI Agent 全自动设计一款 RV32I 最小指令集 CPU，模拟仿真 benchmark，确保设计正确。

**icforge 状态：部分实现** — 三种 RV32I 微架构已存在并通过仿真 + 综合，但**未**经过官方架构测试或形式证明。

| 项目 | 微架构 | 程序数 | 退休指令 | 综合 CPI | 证据 |
|---|---|---|---|---|---|
| `benchmarks/rv32_single` | 单周期 | 114 | 18099 | 1.0009 | [riscv-results.md](docs/riscv-results.md) |
| `benchmarks/rv32_multi` | 多周期 | 114 | 18099 | 3.0018 | 同上 |
| `benchmarks/rv32_pipeline` | 两级流水 | 114 | 18099 | 1.1290 | 同上 |

- 每核 114 个程序（50 定向 + 64 确定性随机），SoC ROM/RAM 签名仿真通过。
- 单周期核已下载到 HX1P35A 板（SRAM-only JTAG，IDCODE `4808f0e7`，bitstream SHA-256 `83ea9b12...`），OpenOCD TDO 校验零错误。
- **板上功能验收未闭合**：R11 串行签名没有外部逻辑分析仪采集，功能分数来自独立仿真周期 + 50 MHz 时序约束折算。
- **未运行**：Spike/官方架构测试、形式证明、CPU 专用 STA 覆盖率未达 100%。

**复现**：
```powershell
.venv\Scripts\python.exe -m fpga_agent.cli verify benchmarks/rv32_single
.venv\Scripts\python.exe -m fpga_agent.cli td --stage synth benchmarks/rv32_single
```

---

## 阶段 3：RVA23 商用级 CPU + signoff + tapeout

**目标**：AI Agent 设计高性能 RVA23 商用级 CPU，完成 signoff 和 tapeout。

**icforge 状态：外部阻塞** — **无法在此环境实现**。

- **tapeout（流片）** 需要商业 EDA、foundry PDK、物理验证、签核流程 — 本环境只有开源 Yosys/nextpnr 和 Anlogic TD，**没有**商业 EDA、**没有** foundry PDK、**没有**流片能力。
- **signoff** 指商业 EDA 的 signoff（DRC/LVS/STA/功耗），icforge 的开源等价物（Yosys synth + Verilator lint + 结构覆盖）是**验证证据**，不是**商业 signoff**。
- **RVA23** 是 RISC-V 的 Linux/高性能 profile，需要完整的 ISA 实现 + MMU + 缓存 + 中断 + 原子指令 — 目前只有 RV32I 子集。
- **诚实声明**：此阶段在本环境**不可完成**。icforge 能证明 Agent **可以**设计并验证 RV32I 级 CPU；RVA23 + tapeout 需要商业 EDA 生态和 foundry 支持，不在开源工具链能力范围内。

---

## 阶段 4：CUDA-compatible Tiny GPU（PTX 极小子集，inference-only）

**目标**：AI Agent 实现 CUDA-compatible tiny GPU，只支持 PTX 非常小子集，实现 minimum inference-only GPU。

**icforge 状态：已实现（最小版）** — `examples/agent_validation/tiny_gpu` fixture 已通过全部 EDA 门禁。

### 设计
- 锁步 SIMT 核心：4 lane，每 lane 8 个 32-bit 私有寄存器 + 16 字私有 dmem bank。
- PTX-like 指令子集：`movi/mov/add/sub/mul/mad/ld/st/setp.lt/bra/exit`（11 条）。
- 共享前端 PC + 每 lane 私有数据通路（无 warp 调度、无分支发散、无缓存层次）。
- `mad` 指令 = `rd = ra*rb + rd`，即推理 MAC。
- 32-bit 算术回绕；`dmem_wren` 仅在 core 空闲时生效（host 加载不与执行竞争）。

### 证据（全部 pass）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 仿真 | `icforge verify examples/agent_validation/tiny_gpu` | `PASS: tiny_gpu runs=7 checks=7`，7 条 `CHECK REQ-NNN PASS` |
| Lint | `icforge lint examples/agent_validation/tiny_gpu` | `Lint: pass` |
| 综合 | `icforge synth examples/agent_validation/tiny_gpu` | 40938 cells / 4978 flops |
| 结构覆盖 | `icforge coverage examples/agent_validation/tiny_gpu` | 3158/3158 RTL points (100.0%) |
| CDC | `icforge cdc examples/agent_validation/tiny_gpu` | `No clocked-process clock/reset domain crossing candidates found.` |

### 验证内容
- REQ-001: 同步复位清除 PC/running/done/所有 lane 寄存器
- REQ-002: core 空闲直到 start 脉冲，然后从地址 0 取指执行
- REQ-003: 每 lane 计算 4 元素点积（含 32-bit 回绕），匹配独立参考模型
- REQ-004: 每 lane 结果存到自己的 dmem bank offset 8，其他 lane/输入字不动
- REQ-005: EXIT 后 done 断言并保持（core 停止）
- REQ-006: MOVI/MOV/ADD/SUB/MUL + SETPLT 真路径定向微测试
- REQ-007: BRA 跳过 + SETPLT 假路径

### 诚实限制
- **不是** CUDA 兼容的 GPU：没有 PTX 汇编器、没有 CUDA runtime、没有 warp 调度。它是**PTX-like** 指令子集的锁步 SIMT MAC 引擎。
- **不是**完整 inference GPU：只支持单层点积类 kernel，不支持卷积、注意力、矩阵分块。
- `icforge lec` 在此 netlist 上 300s 超时（乘法器密集），LEC 未在此 fixture 上声明。

### 复现
```powershell
.venv\Scripts\python.exe -m fpga_agent.cli verify examples/agent_validation/tiny_gpu
.venv\Scripts\python.exe -m fpga_agent.cli lint examples/agent_validation/tiny_gpu
.venv\Scripts\python.exe -m fpga_agent.cli synth examples/agent_validation/tiny_gpu
.venv\Scripts\python.exe -m fpga_agent.cli coverage examples/agent_validation/tiny_gpu
.venv\Scripts\python.exe -m fpga_agent.cli cdc examples/agent_validation/tiny_gpu
```

---

## 阶段 5：商用级 LPU（inference 极致优化，跑开源模型）

**目标**：AI Agent 实现商用级性能较强的全新架构 LPU，inference 极致优化，让开源模型能跑在上面，初步实现 RSI loop 闭环。

**icforge 状态：已实现（最小版）** — `examples/agent_validation/tiny_lpu` fixture 已通过全部 EDA 门禁。

### 设计
- 张量流式处理器（tensor streaming processor）：`tiny_lpu #(NMAC=4, NW=16, DW=32)`。
- 片上权重 SRAM：`NMAC` 个 bank，每 bank `NW` 字，host 经 `wmem_wren/wmem_addr/wmem_wdata` 加载。
- 流式激活输入：`act_valid`/`act_in`，每个时钟沿（`running && act_valid`）并行累加 `acc[g] += wmem[g][idx] * act_in`。
- 共享索引计数器 `idx`，`idx == NW-1` 时 `done` 断言、引擎停止。
- 确定性执行：无调度器、无分支发散、无缓存层次，固定数据流。`start` 时清零所有累加器（每次流式计算都是新计算）。

### 证据（全部 pass）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 仿真 | `icforge verify examples/agent_validation/tiny_lpu` | `PASS: tiny_lpu runs=4 checks=7`，7 条 `CHECK REQ-NNN PASS` |
| Lint | `icforge lint examples/agent_validation/tiny_lpu` | `Lint: pass` |
| 综合 | `icforge synth examples/agent_validation/tiny_lpu` | 17411 cells / 2186 flops |
| 结构覆盖 | `icforge coverage examples/agent_validation/tiny_lpu` | 720/720 RTL points (100.0%) |
| CDC | `icforge cdc examples/agent_validation/tiny_lpu` | `No clocked-process clock/reset domain crossing candidates found.` |

### 验证内容
- REQ-001: 同步复位清除所有累加器、索引、running、done
- REQ-002: 引擎空闲直到 start 脉冲，然后从索引 0 流式计算
- REQ-003: 每个 MAC 累加器等于其权重 bank 与激活流的 32-bit 回绕点积（vs 独立参考模型）
- REQ-004: NW 个激活后 done 断言并保持（引擎停止）
- REQ-005: 第二次流式运行（新权重+新激活）结果正确
- REQ-006: 部分流式（act_valid 中途暂停再恢复）仍正确完成
- REQ-007: 复位清除已完成的累加器和 done

### 诚实限制
- **不是**商用级 LPU：这是单层 GEMV 引擎，不是多算子/多层 transformer 推理系统。无稀疏张量、无混合精度、无片上互连、无确定性调度器的完整实现。
- **没有**跑开源模型：fixture 只验证权重×激活的点积累加正确性，没有模型推理端到端验证。
- **RSI loop 闭环尚未真正跑通**：阶段 6 的"Agent 自训练模型 + 改进芯片"这一步还没开始。

### 复现
```powershell
.venv\Scripts\python.exe -m fpga_agent.cli verify examples/agent_validation/tiny_lpu
.venv\Scripts\python.exe -m fpga_agent.cli lint examples/agent_validation/tiny_lpu
.venv\Scripts\python.exe -m fpga_agent.cli synth examples/agent_validation/tiny_lpu
.venv\Scripts\python.exe -m fpga_agent.cli coverage examples/agent_validation/tiny_lpu
.venv\Scripts\python.exe -m fpga_agent.cli cdc examples/agent_validation/tiny_lpu
```

---

## 阶段 6：Data Center Training GPU + Inference LPU + Agent 自训练模型

**目标**：AI Agent 设计可规模级组建 data center 的 training GPU 和 inference-only LPU；Agent 自己训练模型的同时逐步改进芯片，让 RSI loop 闭环进一步跑起来。

**icforge 状态：已实现（最小版）** — `examples/agent_validation/tiny_train_gpu` fixture 通过全部 EDA 门禁；`fpga_agent/training.py` 实现最小 SGD 自训练闭环（训练权重 → 量化 → 与 RTL 同语义的 GEMM+ReLU 前向验证）。

### 硬件：tiny_train_gpu GEMM + ReLU 计算核

- `tiny_train_gpu #(N=4, DW=32)`：片上 A/B/C 三块 SRAM，host 加载端口，三重嵌套 GEMM 循环（`C = ReLU(A*B)`），`raw` 旁路（不应用 ReLU，保留负和）。
- `start` 清零循环计数器/累加器/running/done 并清空 C 内存（复位路径也清 C）；`done` 在 `N^3` 周期后断言并保持。
- 与阶段 4（SIMT）和阶段 5（GEMV 流式）不同，这是**训练 GPU 的前向计算核心**：矩阵乘法的输入是批量激活和权重，输出是下一层的激活。

### 软件：Agent 自训练闭环（`fpga_agent/training.py`）

- `icforge training`：纯 Python（无第三方 ML 框架）实现单层 ReLU 线性模型的 SGD 训练，在合成数据（`Y = ReLU(X * W_true)`）上训练；训练完成后把权重量化为 int32 固定点，用**与 tiny_train_gpu RTL 完全相同的 32 位回绕 GEMM + 符号位 ReLU** 语义跑前向，并对比 float 参考模型的正/零模式。
- 这是 RSI loop 闭环的软件侧：Agent 训练自己的模型 → 训练出的权重可以被自己的芯片（tiny_train_gpu）执行前向。

### 证据（全部 pass）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 仿真 | `icforge verify examples/agent_validation/tiny_train_gpu` | `PASS: tiny_train_gpu runs=4 checks=7`，7 条 `CHECK REQ-NNN PASS` |
| Lint | `icforge lint examples/agent_validation/tiny_train_gpu` | `Lint: pass` |
| 综合 | `icforge synth examples/agent_validation/tiny_train_gpu` | 6907 cells / 1576 flops |
| 结构覆盖 | `icforge coverage examples/agent_validation/tiny_train_gpu` | 701/701 RTL points (100.0%) |
| CDC | `icforge cdc examples/agent_validation/tiny_train_gpu` | `No clocked-process clock/reset domain crossing candidates found.` |
| 自训练 | `icforge training` | 默认 seed 下 loss `0.2817 -> 0.0333`（-88.1%），量化 int32 前向与 float 参考 100% 一致；`status=pass` |

### 验证内容

- REQ-001: 同步复位清除循环计数器、累加器、running、done 和 C 内存
- REQ-002: 引擎空闲直到 start 脉冲，然后从 (0,0,0) 开始
- REQ-003: 每个 C 元素等于 A、B 的 32-bit 回绕 GEMM 并应用 ReLU（vs 独立参考模型）
- REQ-004: `N^3` 周期后 done 断言并保持
- REQ-005: raw 模式（无 ReLU）保留负和（符号位为 1 的和原样存储）
- REQ-006: 第二次运行（新矩阵）产生正确的 ReLU 结果
- REQ-007: 复位后清除 C 内存和 done

### 诚实限制

- **不是**数据中心级 training GPU：这是单 tile 整数 GEMM + ReLU 核。无大规模矩阵阵列、无高带宽内存子系统、无 NVLink 类互连、无 FP8/BF16 混合精度、无稀疏 GEMM、无多 tile 分块、无训练调度器。
- **不是** PyTorch 类 ML 训练栈：`fpga_agent/training.py` 是纯 Python 玩具级 SGD 循环（单层、固定学习率、无自动微分、无优化器）。它证明 Agent 可以训练自己的模型，不证明它可以训练实用模型。
- **RSI loop 尚未真正跑通**：软件训练与硬件前向的衔接目前是"语义对齐"（同一套 32 位回绕 GEMM+ReLU 数学），不是板上执行训练好的权重；Agent 用训练结果改进芯片设计的闭环（如根据 loss 调整架构参数）尚未实现。

### 复现

```powershell
.venv\Scripts\python.exe -m fpga_agent.cli verify examples/agent_validation/tiny_train_gpu
.venv\Scripts\python.exe -m fpga_agent.cli lint examples/agent_validation/tiny_train_gpu
.venv\Scripts\python.exe -m fpga_agent.cli synth examples/agent_validation/tiny_train_gpu
.venv\Scripts\python.exe -m fpga_agent.cli coverage examples/agent_validation/tiny_train_gpu
.venv\Scripts\python.exe -m fpga_agent.cli cdc examples/agent_validation/tiny_train_gpu
.venv\Scripts\python.exe -m fpga_agent.cli training
```

---

## 阶段 7：半导体级计算架构重新设计

**目标**：针对整个计算架构大刀阔斧重新设计：CPU（SoC 化，含 I/O、protocol、communication、encoding/decoding）、training GPU、各规格/功耗/场景的 inference-only LPU，从半导体层面重新设计整套 RSI 硬件体系。

**icforge 状态：已实现（最小版）** — `examples/agent_validation/tiny_soc` fixture 通过全部 EDA 门禁；这是阶段 7 "SoC 化整合" 方向的最小步（RV32I CPU 作为总线主 + **DMA 引擎（2D 分散/聚集 + 描述符链 + 中断）作为第二总线主** + **AXI-like 握手总线** + **指令缓存（直接映射、阻塞式）** + **数据缓存（直接映射、非阻塞式、写失效一致性）** + memory-mapped 总线 + GEMM 加速器 + UART 串口 I/O），不是半导体级架构重设计。

### tiny_soc：最小 SoC 级整合（RV32I CPU + DMA 双总线主）

- `tiny_soc #(N=4, DW=32, BAUD_DIV=8)`：单时钟域、单复位域的 SoC 块，集成：
  - **CPU（总线主 1）**：阶段 2 的 RV32I 核（`rv32_core`）作为 SoC 总线主，通过其 dmem 端口驱动整条总线；独立的 `cpu_rst_n` 让调试总线可在 CPU 复位保持时使用。测试平台可切换：调试总线优先于 CPU dmem。
  - **DMA 引擎（总线主 2，2D 分散/聚集 + 描述符链 + 中断）**：`tiny_dma` 是被测 fabric 内的独立模块，把 N*N 个 32 位字从源 SRAM 窗拷到目的 SRAM 窗（每周期一字），**逐行（row-by-row）带可配行步长**：源行步长 `DMA_SRCS` 与目的行步长 `DMA_DSTS`（word-indexed），行内 N 字连续。默认步长为 N（即连续 row-major 线性 memcpy）；源步长 ≠ N 时从源聚集（gather）、目的步长 ≠ N 时向目的分散（scatter）。它还支持**描述符链（descriptor chain）**：CPU 在 fabric 的 0x100..0x10F 窗编程一张 4 槽 × 13 位描述符表（每槽含源/目的窗选择、行步长、`last` 与 `next` 指针），`DMA_HEAD`（0x38）指定链头、`DMA_CTRL[1]` 开启链模式；引擎按当前描述符拷贝、沿 `next` 指针走下一个，直到 `last` 描述符，整条链只在结束时中断一次。它有自己的寄存器从口（映射到 0x20-0x3F：DMA_SRC=0x20、DMA_DST=0x24、DMA_SRCS=0x28、DMA_DSTS=0x2C、DMA_CTRL=0x30、DMA_STATUS=0x34、DMA_HEAD=0x38）和一条主读写口进入 fabric 的 SRAM。fabric 在 GEMM 空闲时对 DMA 写相比 host 总线写**优先仲裁**；DMA 的组合读是独立 mux，因此 CPU 可在拷贝进行中轮询 DMA_STATUS。拷贝完成时引擎拉起**中断**（`dma_irq` 输出线 + `DMA_STATUS[2]` 中断标志），保持到下一次 start 清除。这是 SoC 块里真正的**多主 + 中断**行为（CPU 与 DMA 争夺同一 SRAM）。
  - **互连（AXI-like 握手）**：host 写/读总线（`bus_we`/`bus_wstrb[3:0]`/`bus_addr[8:0]`/`bus_wdata`/`bus_rdata`，9 位字节地址）+ 地址译码器（A 窗 0x40-0x7F、B 窗 0x80-0xBF、C 窗 0xC0-0xFF、控制/状态寄存器 0x00-0x10、DMA 从口 0x20-0x3F、DMA 描述符窗 0x100-0x10F）。写侧有 `bus_wready`（= GEMM 空闲，声明操作数 SRAM 写口可用）：GEMM 运行期间 `bus_wready` 拉低，向 A/B 窗的原始写被 fabric 丢弃（寄存器写不占用操作数 SRAM，始终接受）；读侧是组合读，`bus_rvalid` 恒为 1。
  - **计算**：整数 GEMM 加速器 `C = ReLU(A*B)`（32 位回绕 + 符号位 ReLU），片上 A/B/C 三块 SRAM，`start` 脉冲触发 `N^3` 周期 triple-nested 循环，`done` 断言并保持。
  - **串口 I/O**：8N1 UART 发送器（`uart_tx`，`BAUD_DIV` 周期/位，LSB-first，start/8 数据位/stop），`TXDATA` 寄存器触发一帧。
  - **指令内存（IMEM）**：256 字（word-indexed），由测试平台加载；CPU 从 IMEM 取指。
   - **指令缓存（I-cache）**：直接映射 4 行 × 4 字，位于 CPU 取指路径（fabric 内）。RV32I 核组合取指、无 stall 输入，故缓存为**阻塞式**：未命中时 fabric 断言 `imem_stall`、wrapper 经下降沿锁存的门控关 CPU 时钟，refill 期间（每周期一字、4 周期填满一行）下发 NOP（`0x00000013`）防止停摆 CPU 解码出伪访存；命中时组合返回缓存字。probe 窗（总线 0x014 控制：bit9 flush / bit8 probe_en / bits7:0 idx；0x018 数据）可确定性驱动缓存；tag = `imem_idx[7:4]`（16 字块），不同块映射同一行以验证 tag 区分。
   - **数据缓存（D-cache）**：直接映射 4 行 × 4 字，位于 CPU 数据路径（fabric 内）。RV32I 核组合读 dmem、无 stall 输入，故缓存为**非阻塞式**：读未命中时同周期**旁路到 SRAM**（CPU 总能读到正确数据），同时一次性 refill 整行 4 字。一致性用**失效**（无写回）：host/DMA 写 A/B/C 命中字清除该行；GEMM 启动时清空整个缓存（它原地重写 C）；probe 窗（0x01C 控制：bit10 fill / bit9 flush / bit8 probe_en / bits7:2 addr；0x01D 数据）可 flush、显式填充并观测命中/未命中。tag = `addr[7:6]`（A=1/B=2/C=3，0=不缓存），line = `addr[5:4]`，offset = `addr[3:2]`；显式 fill 是必需的，因为 1 周期 refill 会让 probe 周期内 miss/hit 无法区分。
  - **复位**：同步低有效复位清除 done、UART 状态机、GEMM 计数器/累加器和 C 内存；CPU 复位独立；DMA 状态机/寄存器同域复位。
- **结构**：为让结构覆盖可测，RTL 拆成 `rtl/tiny_soc_core.sv`（被测 fabric：互连译码、寄存器、A/B/C SRAM、GEMM、UART、IMEM、DMA 描述符表与接线/仲裁；使用窄的 word-index 接口，每一 bit 都可 toggle 到）、`rtl/tiny_dma.sv`（被测 DMA 引擎：窄的 5 位从口地址/3 位从口数据使每个从口 bit 可 toggle；32 位主数据通路由 TB 灌入多样数据使其全 bit 可 toggle；行步长乘法器与 row/col 计数器由 TB 遍历所有步长值使其全 bit 可 toggle；描述符链的 desc_idx/next/last 状态由 TB 的链扫与零化序言遍历使其可 toggle）与 `rtl/tiny_soc.sv`（wrapper：例化 RV32I CPU + fabric，持有 32 位 CPU 总线与调试/CPU 总线 mux，并透出 `dma_irq` 中断线）。`rv32_core.v` 是复用 IP，自带验证套件（`benchmarks/rv32_single`：114 程序、18,099 条退休指令），本夹具**编译但不重复测量**它。
- 这对应阶段 7 描述的 "SoC 化，包括里面的 I/O、protocol、communication" 的**最小**实现：一个 RV32I 核 + 一个 DMA 引擎在 memory-mapped 总线上共同驱动计算加速器 + 串行协议收发器。

### 证据（全部 pass）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 仿真 | `icforge verify examples/agent_validation/tiny_soc` | `PASS: tiny_soc`，17 条 `CHECK REQ-001..017`（含 CPU 自主运行、DMA 线性拷贝、DMA 2D 分散/聚集、DMA 中断、DMA 描述符链、AXI-like 握手、指令缓存、数据缓存） |
| Lint | `icforge lint examples/agent_validation/tiny_soc` | `Lint: pass` |
| 综合 | `icforge synth examples/agent_validation/tiny_soc` | 54,977 cells / 11,998 flops |
| 结构覆盖 | `icforge coverage examples/agent_validation/tiny_soc` | 2616/2616 RTL points (100.0%)，19 个可证不可达点豁免（见下） |
| CDC | `icforge cdc examples/agent_validation/tiny_soc` | `No clocked-process clock/reset domain crossing candidates found.` |

### 验证内容

- REQ-001: 同步复位清除 done 且 UART 空闲高
- REQ-002: 引擎空闲直到 CTRL start 脉冲
- REQ-003: 每个 C 元素经总线读回等于 32 位回绕 GEMM + 符号位 ReLU（vs 独立参考模型）
- REQ-004: `N^3` 周期后 done 断言并保持
- REQ-005: UART 发送写入的字节为正确的 8N1 帧（start、8 数据位 LSB-first、stop），TB 逐周期采样串口线并解码
- REQ-006: 第二次 GEMM 运行（新矩阵）产生正确结果
- REQ-007: 复位后清除结果寄存器和 done
- REQ-008: **RV32I CPU 自主运行** —— 释放 CPU 后，它自己填充 A/B（全 1）、启动 GEMM、轮询 STATUS、读 C[0]=4 存入 RESULT、发送 UART 0x55、等 UART 空闲、然后 halt
- REQ-009: 负和 GEMM 将 ReLU 结果钳到 0（符号位钳位路径）
- REQ-010: **DMA 引擎（第二总线主，线性拷贝）** —— 默认步长 N 的 A→B 线性拷贝，对每个目的字与多样化的 32 位源数据逐一比对，并检查空闲前的 STATUS、SRC/DST 回读、拷贝中的 busy 与完成后的 done
- REQ-011: DMA 跨其余窗口拷贝（C→A、B→C）并汇报 busy/done；另加寄存器窗扫（src/dst 遍历 0/1/2/0）与一次非法窗（src=3）拷贝以覆盖"无窗口选中"分支
- REQ-012: **DMA 2D 分散/聚集** —— 源行步长 2（聚集，行 r 起于字 r*2）、目的行步长 4（连续），每个目的字等于源窗中对应聚集偏移处的字
- REQ-013: **DMA 中断** —— 拷贝完成后 `dma_irq` 线与 `DMA_STATUS[2]` 断言，下一次 start 清除，再完成后重新断言
- REQ-014: **DMA 描述符链** —— 编程两张描述符：desc0 以源行步长 2 聚集 A→B 并指向 desc1，desc1 线性拷贝 B→C 且标记 last；`DMA_HEAD=0` + `DMA_CTRL=start|chain` 触发整条链：B 等于聚集源字、C 等于 B、链进行中观察到 busy、整条链只在结束时中断一次
- REQ-015: **AXI-like 握手总线** —— `bus_wready` 在 GEMM 空闲时为高、运行期间拉低；运行期间向 A[0] 的原始写被 fabric 丢弃（A[0] 保持其值），而寄存器写（RESULT）因不占用操作数 SRAM 始终被接受；`bus_write` 等待 ready 后才提交
- REQ-016: **指令缓存（直接映射、阻塞式）** —— 冷探测（flush + probe 已编程行）触发未命中 refill（fabric 断言 `imem_stall` 并下发 NOP），refill 期间读 0x018 数据窗走旁路返回 IMEM 原字，完成后返回缓存字；热探测（同索引、不 flush）为干净命中且无 refill；tag 区分映射到同一行的不同 16 字块
- REQ-017: **数据缓存（直接映射、非阻塞式）** —— 冷探测（flush + probe 已编程 A 行，不 fill）为未命中（status valid=0）且同周期旁路返回 SRAM 字；显式 fill 后同行任意 offset 均命中并返回缓存字；host 写同一字清除该行，下次探测 miss 并旁路到新值；B/C tag 区分映射到同一 line 的不同窗口；GEMM 启动清空整个缓存

### 覆盖豁免（诚实记录，不作为 pass）

- `rtl/tiny_soc_core.sv` 的 `imem_rd[0]:1->0` 与 `imem_rd[1]:1->0` 两个 toggle 点在 `coverage.unreachable` 中声明为**可证不可达**：所有合法 RV32I 指令字的 bit [1:0] 恒为 `2'b11`（opcode 字段 [6:0] 恒为奇数、bit 1 置位），因此取指字的低两位恒为 1，任何合法程序都无法让它们 `1->0`。
- DMA 主地址的低两位（`tiny_dma.sv` 的 `m_waddr[1:0]`/`m_raddr[1:0]`，fabric 侧的 `dma_m_waddr[1:0]`/`dma_m_raddr[1:0]`，共 16 个 toggle 点）同样声明为**可证不可达**：DMA 做的是**字对齐**的 32 位拷贝，两个窗口基地址（0x40/0x80/0xC0）与行步长都是 word-indexed（步长按字计、地址按 `偏移<<2` 展开），所以主字节地址 bit [1:0] 恒为 0，任何合法拷贝都无法让它们翻转。
- `rtl/tiny_soc_core.sv` 的 `bus_rvalid:1->0` toggle 点声明为**可证不可达**：fabric 的读是**组合**的（`bus_rdata = f(bus_addr)`，同周期有效），因此 AXI-like 的读有效线 `bus_rvalid` 恒驱 1，永不 `1->0`。
- 豁免点从分母中剔除、记录在报告的 `waived_unreachable` 中并附理由，**不计为覆盖**；报告同时给出 `waiver_unmatched` 以防豁免声明失效（本次 0 条未匹配）。

### 诚实限制

- **CPU 不是新设计**：SoC 里的 RV32I 核是阶段 2 的复用 IP；本阶段新增的是 SoC 化整合（总线主接线、地址译码、外设挂载）与一个**最小 DMA 引擎**，不是新的 CPU 微架构。
- **DMA 是最小 2D 分散/聚集引擎，不是商用控制器**：固定长度 N*N 字、窗到窗、每周期一字、行步长可配（支持 gather/scatter）、支持**描述符链**（4 槽 × 13 位，`DMA_HEAD` 链头 + `next`/`last` 指针）且完成时拉中断（链模式整链只中断一次）；但无多通道、无 QoS/优先级环、无跨时钟、无与 GEMM 的重叠（GEMM 空闲时才仲裁 DMA 写）。它是一个真正的第二总线主 + 中断源，不是商用 DMA 控制器。
- **不是**半导体级架构重设计：这是单时钟、单域的最小 SoC 块。没有完整 AXI/NoC（只有最小 AXI-like 写就绪/读有效握手）、MMU、多时钟域（除 CPU 独立复位外）、电源管理、SerDes/DDR PHY、编码/解码（仅一个 UART 帧）。缓存只是最小可用版：直接映射 4 行 × 4 字的指令缓存（阻塞式）与数据缓存（非阻塞式、写失效一致性），无多级、无写回、无预取、无 MMU 协同。
- **不是**阶段 7 描述的全场景 SoC 矩阵（各规格/功耗/场景的 inference-only LPU 细分）；这里只有一个 RV32I 核 + 一个 DMA + 一个 GEMM 加速器 + 一个 UART。
- **覆盖豁免是"可证不可达"的有界声明，不是覆盖**：19 个点被豁免而非被激励到；这是显式、可审计的边界，不是伪造的 100%。
- **LEC 未运行**：`icforge lec` 在乘法器密集 netlist 上超时（WASM SAT 预算），tiny_soc 同样不声称 LEC。

### 复现

```powershell
.venv\Scripts\python.exe -m fpga_agent.cli verify examples/agent_validation/tiny_soc
.venv\Scripts\python.exe -m fpga_agent.cli lint examples/agent_validation/tiny_soc
.venv\Scripts\python.exe -m fpga_agent.cli synth examples/agent_validation/tiny_soc
.venv\Scripts\python.exe -m fpga_agent.cli coverage examples/agent_validation/tiny_soc
.venv\Scripts\python.exe -m fpga_agent.cli cdc examples/agent_validation/tiny_soc
```

---

## 总结：icforge 当前进度

| 阶段 | 状态 | 说明 |
|---|---|---|
| 1. Agent-driven EDA | ⚠️ 部分实现 | 核心 EDA 流水线存在并通过验证；非商用级；LEC/formal 在乘法器密集 netlist 上受限 |
| 2. RV32I CPU | ⚠️ 部分实现 | 3 种微架构 114 程序通过仿真+综合；未过官方架构测试；板上功能未闭合 |
| 3. RVA23 + tapeout | 🔴 外部阻塞 | 需要商业 EDA + foundry PDK + 流片能力，本环境不具备 |
| 4. Tiny GPU (PTX subset) | ✅ 已实现（最小版） | tiny_gpu fixture 通过 verify/lint/synth/coverage/cdc 全部门禁 |
| 5. LPU | ✅ 已实现（最小版） | tiny_lpu fixture（张量流式 GEMV）通过 verify/lint/synth/coverage/cdc 全部门禁；非商用级，无开源模型端到端验证 |
| 6. Data Center GPU + Agent 训练 | ✅ 已实现（最小版） | tiny_train_gpu fixture（GEMM+ReLU 计算核）通过全部门禁；fpga_agent/training.py 实现 SGD 自训练闭环并与 RTL 同语义前向对齐；非数据中心级、无 PyTorch 类训练栈 |
| 7. 半导体级重新设计 | ✅ 已实现（最小版） | tiny_soc fixture（RV32I CPU 作为总线主 + DMA 引擎（2D 分散/聚集 + 中断）作为第二总线主 + memory-mapped 总线 + GEMM 加速器 + UART + 直接映射指令缓存 + 非阻塞数据缓存）通过 verify/lint/synth/coverage/cdc 全部门禁；这是"SoC 化整合"方向的最小步，非半导体级架构重设计（CPU 是复用 IP，DMA 是最小 2D 引擎，无 AXI/NoC/MMU/多时钟域/PHY） |

### RSI loop 闭环进度

党哥的最终目标：**AI Agent 设计 EDA → AI Agent 打磨 EDA → AI EDA 与 AI Agent 一起进化 → AI Agent 完全自己接手芯片设计 → AI Agent 为自己设计大脑和心脏。**

当前进度：
- **EDA 设计**：icforge 已实现 Agent-driven EDA 的核心流水线（verify/lint/synth/lec/coverage/cdc/timing/release/svsim）。
- **EDA 进化**：每个阶段的新 fixture（RV32I → tiny_gpu → tiny_lpu → tiny_train_gpu → tiny_soc）都驱动了 EDA 能力的迭代（如 coverage 从结构到功能、cdc 从无到有）。
- **芯片设计自主性**：tiny_gpu fixture 的 RTL 由 Agent 设计，TB 由 host 固定，全部门禁由 Agent 的 EDA 验证 — 这证明了 **Agent 可以自主设计并通过自己的 EDA 验证一个 SIMT 架构**。
- **自训练闭环（阶段 6 软件侧）**：`fpga_agent/training.py` 让 Agent 在宿主上训练自己的模型（SGD），并把训练出的权重量化为 int32，用与 `tiny_train_gpu` RTL **完全相同**的 32 位回绕 GEMM + 符号位 ReLU 语义跑前向 — 软件训练闭环已经跑通，Agent 训练自己的模型、自己的芯片可执行前向（语义对齐层面，非板上执行）。
- **SoC 化整合（阶段 7 最小版）**：`tiny_soc` fixture 把**RV32I CPU 与一个 DMA 引擎（2D 分散/聚集 + 中断）作为两个总线主**（CPU 是阶段 2 复用核，通过 dmem 驱动总线；DMA 从 0x20-0x3F 寄存器窗编程，行步长可配支持 gather/scatter，完成时拉中断，fabric 对 DMA 写比 host 总线写优先仲裁）、计算（GEMM 加速器）、互连（memory-mapped 总线 + 地址译码）、串口 I/O（UART）整合进一个单时钟域 SoC 块并通过全部门禁 — 这是阶段 7 "SoC 化（含 I/O、protocol、communication、encoding/decoding）" 方向的最小步，不是半导体级架构重设计。
- **尚未跑通**：RSI loop 的完整闭环（Agent 用训练结果迭代改进芯片架构、板上执行训练好的权重）还没有实现。阶段 7 的半导体级架构重设计（晶体管级优化、封装协同设计、PPA 全空间探索）尚未开始。

### 下一步（按路线顺序）

1. **阶段 7 深化**：在 tiny_soc 基础上扩展 SoC 块（DMA 描述符链、AXI-like 握手总线、直接映射指令缓存与数据缓存已完成，下一步加 MMU/虚拟内存、多时钟域、存储子系统），并从半导体层面做架构重设计（晶体管级优化、PPA 全空间探索）— 依赖前面所有阶段的能力积累。
2. **阶段 2 收尾**：RV32I 补充官方架构测试（Spike 或 ISA 测试套件）的仿真验证。
3. **阶段 1 深化**：LEC/formal 的缩放性改进（并行 SAT、增量等价）。
4. **阶段 3**：外部依赖满足后才可能开始（商业 EDA + foundry）。
5. **阶段 6 深化**：训练闭环从"语义对齐"升级为板上执行（需要把 tiny_train_gpu 实例化到 SoC、加载训练好的权重、运行推理）。

---

*本文档是诚实的进度映射，不是营销材料。每个状态都附可复现的命令和结果。*
