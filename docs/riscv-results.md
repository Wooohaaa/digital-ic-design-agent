# RISC-V CPU 基准与 HX1P35A 部署结果

生成时间：2026-11-04 21:43（UTC+8）。目标 PH1P35MDG324 / HX1P35A，TD V6.2.168116，50 MHz。

| 结构 | 独立程序 | 提交指令 | 仿真周期 | 综合 CPI | 50 MHz 折算 MIPS | LUT6 | 寄存器 | Setup/hold WNS ns | STA覆盖率 | 交付 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 单周期 | 114 | 18099 | 18115 | 1.0009 | 49.96 | 2004 | 2529 | 0.463 / 0.073 | 92.36% | [ZIP](../benchmarks/rv32_single/deliverables/rv32_single-delivery.zip) |
| 多周期 | 114 | 18099 | 54329 | 3.0018 | 16.66 | 2727 | 3532 | 7.284 / 0.034 | 92.24% | [ZIP](../benchmarks/rv32_multi/deliverables/rv32_multi-delivery.zip) |
| 两级流水线 | 114 | 18099 | 20434 | 1.1290 | 44.29 | 2837 | 3333 | 0.696 / 0.039 | 92.85% | [ZIP](../benchmarks/rv32_pipeline/deliverables/rv32_pipeline-delivery.zip) |

2026-11-04 又复跑了三种 CPU 的同一基准，114/114 程序均通过；SoC ROM/RAM 签名仿真也全部通过。综合 CPI 为整个固定程序集合的 `总周期 / 总退休指令`，MIPS 是 `50 MHz / CPI` 的理论折算值，不是 CoreMark 分数，也不是板上计时器直接测得的吞吐率。单周期核在这个工作集上分数最高，最终已将它下载到 HX1P35A。

实板下载通过：SRAM-only 配置，JTAG FPGA IDCODE `4808f0e7`，bitstream SHA-256 `83ea9b12cdbf205f59801e1556e38d73ba7514530e630e82f1a7ce7a32fe51f7`，OpenOCD 严格 TDO 校验零错误。板上 SoC 会运行 ROM 基准并由 R11 输出串行签名；本次没有逻辑分析仪采集 R11，因此硬件证据确认的是位流配置成功，功能分数来自独立仿真周期及 50 MHz 时序约束折算。结构化本次结果见 [HX1P35A CPU 运行记录](riscv-hx1p35a-run.json)。

三份初始候选来自此前的 gpt-6-luna exchange 生成记录；本轮重新验证已有 RTL，没有发起新的在线模型调用。每份包含 50 个定向程序和 64 个确定性随机程序，另验证用于综合的 ROM/RAM SoC 签名。所有最终 setup/hold TNS 均为 0。资源统计包含板级 SoC 测试封装。

工程签核尚未授予：本机目前已安装 Verilator 5.048，但这份历史 CPU 运行记录没有对应的严格 lint 结果；CPU STA 覆盖率也未达 100%，时钟复位/例外约束仍需逐项审查。单周期核已完成 SRAM-only JTAG 配置，但 R11 功能签名没有外部采集，因此板上功能验收未闭合。审查方法见 [engineering-workflow.md](engineering-workflow.md) 和[当前工程状态](engineering-validation.md)。

仿真周期是整个回归集合的总数，包含终止/异常检测和流水线填充/冲刷；不能直接作为 CoreMark 或系统性能指标。

详细状态、覆盖点、位流和交付包 SHA-256 见 [suite-report.json](../benchmarks/suite-report.json)。单工程原始报告位于各 `reports/simulation.json` 和 `reports/td.json`。

多周期核的原始 SRAI 失败反例保存在 `benchmarks/failures/rv32_multi_initial`，修复记录见 [repair_001.json](../benchmarks/rv32_multi/generation/repair_001.json)。

尚未跑 Spike/官方架构测试或形式证明；工程审查还要求关闭 STA 覆盖率、时钟/IO 等未决项。执行环境与重现步骤见 [riscv-flow.md](riscv-flow.md)。
