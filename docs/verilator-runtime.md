# 开源 Verilator 运行后端

日期：2026-11-18。功能入口 `sv-check --backend verilator`，也可在 Agent 和 TUI 中显式选择。Icarus 保持默认，没有自动切换后端或合并各后端的通过结果。

- slang 独立解析与展开；Verilator 生成 C++，GNU Make/G++ 构建本地程序，再用独立 Python 参考逐行比对运行观察。运行使用 `--timing --assert` 和默认二态模式；构建固定 `-std=c++20 -O0`，不测仿真性能。
- schema 4 将工具版本、生成物和可执行文件、编译/构建/运行日志与同一份契约和源快照关联。C++ 构建失败保留为 `error`；语义运行失败与前端拒收都单独记录。旧 schema 证据作为历史快照保留，使用原版本核验。
- 公共 CI 配置独立的 Linux 开源运行作业，固定 OSS CAD Suite Linux x64 资产及 SHA-256。作业只验收明确列出的 9 项：整数、packed、package、generate、NBA、数组、非法语法拒收、类虚调用、interface/modport。保留 Icarus 四态作业；Linux 作业未在本轮远端运行。

## 本地复现

用 OSS CAD Suite 2026-11-17，将其 `bin` 放到 `PATH`；Windows 可直接发现 `verilator_bin.exe`，需要按工具包设置 `VERILATOR_ROOT` 指向 `share/verilator`，并将发行包 `lib` 和 GNU Make/C++20 工具所在目录加入 `PATH`。本轮用 MinGW-w64 GCC 16.2.0 (UCRT/POSIX) 和 GNU Make 4.4.1，Git for Windows 提供 makefile 使用的 `sh`、`rm` 与 `uname`。同组工具的默认 `-Os` 构建复现了 `std::basic_string` 移动构造链接失败；`-O0` 构建通过。工具包只保存在本地，下载校验信息见工作记录。

同时跑 LEC 全量回归时，将 `verilator_bin.exe` 单独放入一处 PATH 目录，并保留上述 `VERILATOR_ROOT` 和运行库设置。本机将工具包整个 `bin` 暴露给项目时，LEC 优先找到与当前环境的运行库不兼容的 `yosys.exe`，掩盖了已验证的 YoWASP Yosys。

```sh
uv sync --locked --all-extras
uv run --locked --all-extras python -m fpga_agent sv-check --backend verilator \
  --timeout 180 --output reports/open-sv-common.json --case SV-INT --case SV-PACKED \
  --case SV-PACKAGE --case SV-GENERATE --case SV-NBA --case SV-ARRAY \
  --case SV-REJECT --case SV-CLASS --case SV-INTERFACE
uv run --locked --all-extras python scripts/run_sv_matrix.py --backends icarus modelsim verilator \
  --output reports/sv-three-backends.json --timeout 180
```

第一条 `sv-check` 仅验收所选用例，因此即使 `status=pass`，也应为 `baseline_status=not_complete`（没有选择四态必选用例）。三后端矩阵需要本机全部仿真器与 [固定的官方 UVM 源码](uvm-profile.md)。矩阵可能按设计返回非零；报告保留各后端的实际结果，失败报告仍可通过证据完整性复核。有限样例和外部仿真器都不构成完整 SV、最小 UVM 或自研语言实现的认证。

## 2026-11-18 v0.2 同输入实测

| 后端 | 通过 / 总数 | 未通过观察 |
| --- | ---: | --- |
| Icarus 13.0 | 8 / 18 | 类虚调用 `12/18/9`；interface 和八项 UVM 编译拒收 |
| ModelSim 10.6e | 18 / 18 | 有界 UVM profile 通过 |
| Verilator 5.055 devel | 16 / 18 | 四态 16 行有 12 行与 oracle 不符；UVM fatal 后仍执行了下一条语句 |

Verilator 运行了八项官方 UVM 用例，其中七项通过。`UVM-FATAL` 已打印预期的 fatal 消息及 `error=0`、`fatal=1`，但 `$finish` 之后仍打印了 `FAIL UVM-FATAL returned after fatal`。继续执行的证据使该用例和整个 UVM profile 不能通过。源码、官方库和期望未为此调整。

v0.3 单项复测：同版 Verilator 通过了新增的 [UVM 仲裁/响应探针](uvm-profile.md)，本轮未重跑其余 18 项。
