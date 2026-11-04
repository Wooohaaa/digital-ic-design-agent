# 当前验证结果

v0.2 的三份 RISC-V CPU 最新验收见 [riscv-results.md](riscv-results.md) 和 [riscv-flow.md](riscv-flow.md)。下文保留最初心跳工程的历史验收记录。

本机验收日期：2026-11-04。单个工程的机器可读结果、原始日志保存在示例工程 `reports/` 目录。

| Gate | 结果 | 证据 |
|---|---|---|
| 工程初始化 | 通过 | `hx1p35a-heartbeat/project.json` |
| Icarus 编译与自检仿真 | 通过，PASS | `hx1p35a-heartbeat/reports/simulation.json`、`simulation.log` |
| TD synthesis | 通过 | `hx1p35a-heartbeat/reports/td.json`，TD 6.2.1 / V6.2.168116 |
| TD place/route | 通过，WNS 非负，bitstream 已生成 | `hx1p35a-heartbeat/reports/td.json`、TD `phy_1/final_timing.rpt` |
| HX1P35A 实板观察 | 未执行 | 需实际板卡、JTAG 下载器和屏幕/背光负载 |

最终路由时序：setup WNS **16.332 ns**、TNS **0 ns**；hold WNS **0.154 ns**、TNS **0 ns**。资源报告：48/21,216 LUT6、33 个寄存器、0/40 DSP、0/108 ERAM、3/160 IO。TD 采用默认 speed grade 3。

交付包：[hx1p35a-heartbeat-delivery.zip](../hx1p35a-heartbeat/deliverables/hx1p35a-heartbeat-delivery.zip)，包含 `.al` TD 工程、输入约束、Verilog、testbench、最终 bitstream、时序/资源报告与仿真记录。包内 bitstream 大小约 1.79 MB。

模型 API 的在线 RTL 生成/错误修复路径已实现，但本机没有配置 API Key/model ID，因此本轮没有向在线模型发起调用；实际仿真、TD 综合、布局布线和交付包均由本地心跳样例完成。

实板检查至少确认上电复位、R11 `O_screen_pwm` 心跳输出约 1 Hz，TD timing summary 无未接受的负 slack，器件/封装和 IO 标准与开发板一致。R11 映射来自社区工程，编程前请按实际 HX1P35A revision 与官方原理图复核。下载与 Flash 编程由用户手动完成。
