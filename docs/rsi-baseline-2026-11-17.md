# RSI Chip 本轮基线核查

本文保留 2026-11-17 首轮历史基线。后续时序 LEC 进展见 [第二轮证明契约及结果](lec-state.md)；首轮“时序未证明”的结论不再代表新增方法的当前能力。

日期：2026-11-17（Asia/Shanghai）。本地继续开发的起点为
`Wooohaaa/digital-ic-design-agent` main：`fa8368a37202cf38ab9211f99edc9b6dfb2f39d3`。
首轮核查在保留上游历史和 MIT 许可证的克隆中进行。下文的结果和计划记录当时的状态。

## 实现核查

- Python 3.11+ 的 IC Forge 0.6.0：CLI、Textual 工作台、候选生成/有限修复、持久任务、外部 CLI 编排和工程证据门禁。
- `riscv.py`、`cpu_bench.py`：独立 Python ISA 参考、受保护 testbench 和退休/存储/最终内存/SoC 签名检查。
- `verify.py`：Icarus 仿真；`quality.py`：Verilator lint 和工程审查；`td.py`：厂商 FPGA 实现适配器。
- 上游已有三种 RV32I、HDMI 和 TinyStories 工程及历史报告。本轮只重新运行下面列出的项目；没有重新验收硬件、TD、HDMI、TinyStories 或商用 ASIC。
- `RSI-Labs/RSI-Chip` dev README 是 Rust EDA 的参考材料；没有在本轮导入或编译该仓库，其 SV/UVM/LEC 声明仍属外部项目声明。

## 本机实测

| 项目 | 结果 | 边界 |
| --- | --- | --- |
| 完整 Python 回归（含新增 LEC/交付/Agent 测试） | 204 passed，1 skipped；96.87 s | 跳过项为 Windows symlink 权限；测试含 mocks，不等于每个硬件后端实测 |
| RV32I multicycle | 114 程序、18,099 退休指令、64 随机种子、SoC signature pass | 使用当前仓库 EEI 和 Python oracle |
| RV32I two-stage pipeline | 114 程序、18,099 退休指令、64 随机种子、SoC signature pass | 同上 |
| RV32I single-cycle | 114 程序、18,099 退休指令、64 随机种子、SoC signature pass | 同上 |
| 8-bit adder | 131,072 输入穷举仿真 pass；9 个等价点直接 SAT pass | generic netlist，无物理库 |
| FIFO / sequential counter LEC | 未证明，保留 fail | 当前直接 SAT 路径不足以关闭时序状态关系；不是已确认的 RTL 缺陷 |
| 新增 LEC GitHub CI job | 已配置 | 未在远端执行，不声称 CI 已绿 |

工具：Python 3.11.15、pytest 8.4.2、Icarus 13.0 stable、YoWASP Yosys
`0.71.0.0.post1285`（Yosys 0.71，git `b4c586fd3`）。本轮使用项目内 `.venv`，未改全局工具配置。
新增 LEC checker 对输入和自身代码的指纹变化敏感，最后路径规范化调整后又单独重跑 LEC 专项回归。

证据位于工作区 `validation/2026-11-17-pytest.xml`、`validation/2026-11-17-cpu-suite.json`、
`validation/2026-11-17-summary.json`；逐核原始报告位于各工程的 `reports/simulation.json`，
LEC 产物位于其报告所指向的 `build/lec/<run-id>/`。上游跟踪的 `benchmarks/suite-report.json` 保留原版，避免把本机复测替代历史 TD 报告。

## 复现

在仓库根目录执行。将 Icarus 的 `iverilog` 和 `vvp` 加入 PATH。

```powershell
uv sync --locked --all-extras
uv run --locked --all-extras --with yowasp-yosys==0.71.0.0.post1285 python -m pytest -q
uv run --locked python -m fpga_agent cpu-suite
uv run --locked python -m fpga_agent verify examples/lec/adder
uv run --locked --with yowasp-yosys==0.71.0.0.post1285 python -m fpga_agent lec examples/lec/adder
```

Windows 沙箱的回环套接字限制曾使 asyncio 测试停在初始化，原生 Icarus 编译子进程也曾失败；上述正式通过结果来自允许这些本地操作的执行环境。短 TEMP 路径用于避免沙箱临时目录触发 Windows 长路径限制。

## 下一步

首先完成显式 reset/init/state relation 的时序 LEC，再从计数器/FIFO 扩展到 CPU。
并行主题可按任务需要开展 SV/UVM 支持矩阵和官方 RISC-V 架构验证；当前没有实现完整 SV、UVM、ASIC signoff、PTX GPU 或 LPU。
