# RISC-V CPU 实测结果

生成时间：2026-11-04T16:02:30.443064+08:00。目标 PH1P35MDG324 / HX1P35A，TD V6.2.168116，50 MHz。

| 结构 | 独立程序 | 提交指令 | 仿真周期 | LUT6 | 寄存器 | Setup WNS ns | Hold WNS ns | STA覆盖率 | 交付 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 单周期 | 114 | 18099 | 18115 | 2004 | 2529 | 0.463 | 0.073 | 92.36% | [ZIP](../benchmarks/rv32_single/deliverables/rv32_single-delivery.zip) |
| 多周期 | 114 | 18099 | 54329 | 2727 | 3532 | 7.284 | 0.034 | 92.24% | [ZIP](../benchmarks/rv32_multi/deliverables/rv32_multi-delivery.zip) |
| 两级流水线 | 114 | 18099 | 20434 | 2837 | 3333 | 0.696 | 0.039 | 92.85% | [ZIP](../benchmarks/rv32_pipeline/deliverables/rv32_pipeline-delivery.zip) |

三份初始候选来自此前的 gpt-6-luna exchange 生成记录；本轮重新验证已有 RTL，没有发起新的在线模型调用。每份包含 50 个定向程序和 64 个确定性随机程序，另验证用于综合的 ROM/RAM SoC 签名。所有最终 setup/hold TNS 均为 0。资源统计包含板级 SoC 测试封装。

工程签核尚未授予：Verilator 未安装，STA 覆盖率未达 100%，原始需求文档、时钟复位/例外约束及实板验收仍需关闭。当前结果表示受支持范围的回归和 FPGA 产物证据通过。审查方法见 [engineering-workflow.md](engineering-workflow.md)。

仿真周期是整个回归集合的总数，包含终止/异常检测和流水线填充/冲刷；不能直接作为 CoreMark 或系统性能指标。

详细状态、覆盖点、位流和交付包 SHA-256 见 [suite-report.json](../benchmarks/suite-report.json)。单工程原始报告位于各 `reports/simulation.json` 和 `reports/td.json`。

多周期核的原始 SRAI 失败反例保存在 `benchmarks/failures/rv32_multi_initial`，修复记录见 [repair_001.json](../benchmarks/rv32_multi/generation/repair_001.json)。

本轮没有下载开发板，尚未跑 Spike/官方架构测试或形式证明。执行环境与重现步骤见 [riscv-flow.md](riscv-flow.md)。
