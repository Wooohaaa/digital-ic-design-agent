# IC Forge 本地目录导航

本项目原路径为 `ICT/fpga-design-agent`，现整理到 `ICT/icforge`。Git 仓库、源码、虚拟环境、任务记录和验证证据保持原样；原路径由隐藏兼容入口保留，历史任务与报告中的绝对路径仍可解析。

终端任意位置输入 `icforge` 即可启动。也可双击本目录的 `Start-ICForge.cmd`。功能和完整用法见 [README.md](README.md)。

| 目录 | 内容 |
| --- | --- |
| `fpga_agent/` | agent、TUI、调度、模型接入和验证工具源码 |
| `benchmarks/` | RISC-V CPU、TinyStories、HDMI 旋转立方体及各自证据和交付 |
| `docs/` | 使用说明、开发记录、实板结果和 TUI 图片 |
| `deliverables/`、`dist/` | 已生成的 agent 分发文件 |
| `benchmarks/hdmi_cube/deliverables/` | 立方体可烧录位流、交付 ZIP 和实板演示 MP4 |
| `media/motion-film/` | 从工作区根目录迁入的 IC Forge 宣传片、历次版本、素材和渲染源码 |
| `tests/` | 自动化回归测试 |
| `.fpga-agent/` | 持续任务、执行记录与恢复状态 |
| `.tools/`、`.venv/` | 工具链和 Python 依赖 |
| `tmp/`、`build/`、`reports/` | 原有实验、构建和诊断记录；保留以便追溯 |

这次只整理项目归属和入口，没有搬动工程内部的报告或依赖文件，以免破坏证据哈希、虚拟环境和长期任务的恢复路径。
