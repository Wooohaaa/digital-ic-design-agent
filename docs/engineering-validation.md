# IC Forge 0.6.0 工程验证状态

更新日期：2026-11-07。本文汇总源码、当前本机工具和已有工程证据；它不把历史试验自动升级为本次回归，也不授予工程签核。

## 软件与工具链

| 项目 | 固定/实测版本 | 当前状态 |
|---|---|---|
| IC Forge | 0.6.0；发行包名已统一为 `icforge`，Python 导入路径仍兼容 `fpga_agent` | 已更新 `pyproject.toml` |
| Python | 最低 3.11；本机 3.11.9；CI 矩阵 3.11/3.12 | 依赖解析锁在 `uv.lock` |
| Icarus Verilog | 本机 12.0 devel；CI 安装 Ubuntu 仓库版本 | 可执行文件在 `.tools/icarus/bin/iverilog.exe`，本机未加入 PATH |
| Verilator | 本机 5.048 / MSYS2 UCRT64 | 已安装；历史 `blocked` 报告需通过本次严格 lint 刷新 |
| Anlogic TD | 本机 6.2.168.116 | Windows 本机可用；GitHub runner 不含该厂商工具 |

CI 定义于 [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)：Python 3.11/3.12 × Windows/Linux 单元回归；独立 Ubuntu 作业安装 Icarus 与 Verilator，并对 FIFO、SPI、APB 三个固定夹具执行仿真和严格 lint。主线提交 `fa8368a37202cf38ab9211f99edc9b6dfb2f39d3` 的最新 GitHub Actions 全部通过：[本次运行](https://github.com/Wooohaaa/digital-ic-design-agent/actions/runs/42375073989)。Anlogic TD 未纳入托管 runner，仍由本机工程门禁负责。

本次 agent 评价闭环和 mutation audit 候选改动尚未运行 Python/HDL 回归；下方的回归数字只代表先前已测基线，不代表这些新改动已验收。

## 本机回归与覆盖率

2026-11-07 在 Windows / Python 3.11.9 使用已锁定依赖运行完整回归：**193 passed**，总 statement coverage **67%**。关键 agent/工具路径的行覆盖率如下：

| 模块 | 覆盖率 | 说明 |
|---|---:|---|
| `cli.py` | 52% | 新增 CLI/parser 测试；复杂 TD 与硬件分支仍有空白 |
| `autonomy.py` | 79% | 长任务恢复、后台 worker 和覆盖变量隔离已纳入回归 |
| `orchestration.py` | 86% | 调度、隔离 worktree、审查、暂停恢复测试 |
| `task_planner.py` | 86% | 结构化决策、工作区和凭据脱敏测试 |
| `agent_backends.py` | 87% | 由 orchestration adapter 测试覆盖 |
| `ftdi.py` | 84% | Windows CI 通过模拟 D2XX 驱动测试，不依赖接入真实板卡 |
| `bitbang_relay.py` | 84% | 大块双向 socket 传输测试 |
| `hardware_evidence.py` | 73% | CRC、logits、UART 和 checker 指纹失效场景 |
| `cpu_agent.py` | 67% | CPU 候选接口与修复流程测试 |
| `context_budget.py` | 68% | 保留用户约束与超限拒绝测试 |

本机还对 `sync_fifo`、`spi_master`、`apb_pwm_timer` 各执行了真实 Icarus 自检仿真和 Verilator 5.048 `-Wall` lint，六个阶段均通过。此处覆盖率是 Python agent 代码覆盖，不等于 RTL 功能覆盖；尚未达到 100%，也不构成芯片签核。

## 已有硬件/设计证据

- **RV32I**：单周期、多周期、两级流水线均有 114 个程序、18,099 条退休指令的独立参考比对及 TD 实现记录。TD 报告 STA 覆盖率分别为 92.36%、92.24%、92.85%；正 setup/hold WNS 和零 TNS 不代表约束完整。单周期核曾通过 JTAG SRAM 配置，但 R11 串行签名没有外部采集，因此不把仿真分数说成板上测得性能。详见 [CPU 结果](riscv-results.md)。
- **HDMI 立方体**：两次独立 SRAM 重载，采集卡在等待锁定后分别接受 18/18 与 30/30 个视频样本，均识别到多个旋转姿态。setup/hold WNS 为 +5.746/+0.066 ns，TNS 为 0；默认 RGB(1,1,1) 有实板证据。RGB(0,0,0) 仍会在当前链路失锁，根因未证实。详见 [HDMI 记录](hdmi-cube-flow.md)。
- **TinyStories260K**：模型完整性、logits 和生成 token 有板上验收记录；其处理器/固件范围及样本见 [TinyStories 记录](tinystories-flow.md)。

这些证据来自不同日期和不同工程配置，不可合并成一次全仓通过。相关交付当前仍把 `engineering_signoff` 标为 `not_granted`。

## 未关闭事项

1. CPU STA 未覆盖端点、IO delay、时钟/复位例外和板卡 revision 尚未逐项对照官方资料闭合。HDMI 一项的高覆盖率不能替代 CPU 约束审查。
2. 尚未集成 Spike / RISC-V Architectural Certification Tests，也没有 RV32I 形式证明或 LEC。
3. CDC/RDC、post-route 仿真、RTL 功能覆盖闭合和 ASIC 物理签核尚未完成。
4. 普通 RTL 的模型生成 testbench 仍需要独立审阅；generic Icarus 项目现在可用 mutation audit 诊断常见运算符错误的检出能力，但不判定等价 mutant，也不替代规格审阅、CPU/视频专用验证或 formal。
5. HX1P35A 引脚表仍需对照所用板卡 revision 的官方原理图复核。
6. RGB(0,0,0) HDMI 失锁尚无电气或接收端根因结论。
7. Python agent 总覆盖率为 67%；`cli.py`、`tinyllm.py`、`tinyllm_hardware.py`、`ui_worker.py`、视频捕获及 programmer 的部分错误分支仍需补测。
8. CI 当前已通过 Windows/Linux × Python 3.11/3.12 矩阵及三个 HDL 夹具；Anlogic TD full 和板级功能验收仍需在安路本机/实际开发板执行。

## 复现命令

```powershell
uv sync --locked --all-extras
uv run --locked --all-extras --python 3.11 pytest --cov=fpga_agent --cov-report=term-missing
uv run python -m fpga_agent verify examples/agent_validation/sync_fifo
uv run python -m fpga_agent lint examples/agent_validation/sync_fifo
```

完整逐项门禁见[工程审查流程](engineering-workflow.md)，本地工具版本见[`toolchain.json`](../toolchain.json)，机器可读摘要见[`engineering-validation-current.json`](engineering-validation-current.json)。
