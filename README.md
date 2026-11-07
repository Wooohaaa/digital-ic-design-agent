# IC Forge

IC Forge 是面向数字 IC/FPGA 工程的终端 agent：从需求、RTL 生成与修复，到仿真、lint、安路 TD 实现、证据审查和交付归档。它也能把有明确文件范围的工作交给本机 Codex、Claude Code 或 OpenCode，再由宿主工程门禁验收。

## 快速开始

需要 Python 3.11 或更高版本。安装后可以从任意终端目录运行 `icforge`；当前目录会作为默认工作区。

```powershell
python -m pip install -e .
icforge
```

首次进入后，使用 `/connect` 配置模型，`/project` 选择或创建工程。终端命令和迁移说明见[工作台指南](docs/terminal.md)。

![IC Forge 终端工作台](docs/images/icforge-tui-powershell.png)

### 本地 FPGA 工具

只用 Python 和仿真时不需要 Anlogic TD。完整 HX1P35A 流程还需要 Icarus Verilog 12+、Verilator 5+ 和 Anlogic TD 6.2.x。TD 是 Windows 专用工具，当前版本没有在 GitHub 托管 runner 上运行。

```powershell
python -m pip install -e ".[test]"
$env:ANLOGIC_TD_ROOT = 'C:\Anlogic\TD_6.2.1_Engineer_6.2.168.116'
icforge --help
```

## 一条常用流程

```powershell
icforge init my-design
icforge design my-design --brief-file requirements.md --provider api --model gpt-6-luna
icforge verify my-design
icforge lint my-design
icforge review my-design
icforge flow my-design
```

`flow` 会在当前验证证据有效时运行 TD 实现并打包。没有工具或未关闭的人工审查会作为明确状态保留；`artifact_ready` 不等于工程签核。详细门禁见[工程审查流程](docs/engineering-workflow.md)。

普通 Icarus testbench 通过后，可用 `icforge mutation-audit PROJECT` 抽样注入 RTL 运算符错误，量化 testbench 检错能力；命令在临时副本运行，不改工程源文件。长期任务的成功率、失败工具和耗时可用 `icforge task metrics` 汇总。

## 工程验证记录

- [当前验证状态与未关闭事项](docs/engineering-validation.md)
- [实验索引与复现入口](docs/experiment-log.md)
- [三种 RV32I CPU 结果](docs/riscv-results.md) · [流程和指令范围](docs/riscv-flow.md)
- [HX1P35A HDMI 立方体实板记录](docs/hdmi-cube-flow.md)
- [TinyStories260K 实板推理记录](docs/tinystories-flow.md)
- [Agent 并行调度回归夹具](docs/agent-validation-roadmap.md)
- [RTL mutation audit](docs/rtl-mutation-audit.md) · [Agent 任务评估](docs/agent-evaluation.md)

## 开发与 CI

```powershell
uv sync --locked --all-extras
uv run --locked --all-extras --python 3.11 pytest --cov=fpga_agent --cov-report=term-missing
```

GitHub Actions 覆盖 Python 3.11/3.12 与 Windows/Linux，并在 Linux 作业安装 Icarus 和 Verilator，运行三个自检 RTL 夹具。Python 包依赖由 [`uv.lock`](uv.lock) 固定；本地实测 EDA 工具版本和 TD 平台边界见 [`toolchain.json`](toolchain.json)。

## 许可

IC Forge 使用 [MIT License](LICENSE)。
