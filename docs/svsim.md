# 最小 class 化（UVM 风格）验证环境

`icforge svsim PROJECT` 用 Verilator `--binary` 构建并运行一个**最小、自研的 class 化验证环境**：随机事务类、绑定 `virtual interface` 的 driver、以及自检 scoreboard。

它不是 UVM 库。没有 factory、config_db、sequence、phase、TLM 或功能覆盖率。它只回答一个有哈希证据的问题：**这份 class 化环境能否在真实 RTL 上 elaborate 并执行完成？**

## 为什么需要单独一条路径

主门禁是 Icarus 自检仿真（`icforge verify`），但 **Icarus 不解析 SystemVerilog class**，所以 class 化环境无法在 Icarus 里运行。Verilator 既能 elaborate class，也能生成可执行文件，因此 `svsim` 用 Verilator。三者的分工见 [SystemVerilog 支持范围](systemverilog.md)。

## manifest 配置

工程在 `project.json` 里声明环境：

```json
"svsim": {
  "top": "tb_alu8_env",
  "sources": [
    "rtl/alu8.sv",
    "verif/alu8_if.sv",
    "verif/alu8_txn.sv",
    "verif/alu8_driver.sv",
    "verif/alu8_scoreboard.sv",
    "verif/tb_alu8_env.sv"
  ]
}
```

- `top`：环境顶层模块（含时钟、DUT 例化与 `initial` 激励）。
- `sources`：参与编译的全部工程内 HDL（相对路径，`/` 分隔）。`rtl/` 与 `verif/` 文件都从这里收集并哈希。
- 可选 `cflags`：传给 Verilator `-CFLAGS` 的优化开关，默认 `-O2`。

`--top` 可在命令行覆盖顶层；未声明 `sources` 时命令报错，不会猜。

## 工具链要求

`svsim` 需要：

- Verilator（原生 `verilator` / `verilator_bin`，或 `ICFORGE_VERILATOR` 覆盖）；
- 一个 C++ 编译器（`g++` / `c++` / `clang++`）；
- 一个 make 程序（`make`，Windows 上退化为 `mingw32-make`）。

Verilator 的根目录会自动从可执行文件位置推导（也可用 `ICFORGE_VERILATOR_ROOT` 覆盖）。在 Windows 的 MSYS2 布局下会额外把子系统 `bin` 与 `usr/bin`（提供 `uname`、`python3`、`ar`）加入 `PATH`。

三者任一缺失时命令报 `blocked` 并给出原因，**绝不报 pass**。

### 关于 `-CFLAGS -O2`

Verilator 的构建默认用 `-Os` 编译自己的运行库。在本机 MSYS2 UCRT64 / GCC 16.1 上，`-Os` 会生成对 libstdc++ 移动构造符号的未定义引用（链接失败）。`-O0`/`-O1`/`-O2` 都能正常链接，因此默认传 `-O2`。这是本机工具链的实测规避，见 `docs/engineering-validation.md`。

## 报告与状态

`reports/svsim.json` 记录状态、顶层、源列表、工具版本、C++/make 路径、命令、逐需求 checks、输入哈希与 `reason`。状态语义：

| 状态 | 含义 |
|---|---|
| `pass` | 环境构建成功并运行完成；`spec/verification-plan.json` 每个需求恰好一条 `CHECK REQ-NNN PASS`，且无 `FAIL`/`FATAL`/`ERROR` 行 |
| `fail` | 运行非零退出、超时、出现失败行，或需求检查缺失/重复 |
| `error` | 工具链齐全但 Verilator 构建失败（`-Wall` 警告即视为失败） |
| `blocked` | 缺少 Verilator / C++ 编译器 / make |

补充约定：

- `--binary` 一律叠加 **`-Wall`**，与 `icforge lint` 同样的严格度；有警告即构建失败，不静默容忍。
- 报告绑定 RTL、`verif/` 源文件、`spec/` 规格与验证计划的哈希。改动任一输入即使 `reports/svsim.json` 过期，门禁不会把它当作当前证据。
- `pass` 不代表任何**功能覆盖率**；它只说明这套有界激励按计划跑通。

## 审查门

`DV-008`（verification，`required: false`）映射该报告：缺报告记 `not_run`，工具缺失记 `blocked`，失败记 `blocked`（附原因），当前输入上的通过记 `pass`。它是可选门，不阻断 `artifact_ready`。

## 夹具

`examples/agent_validation/sv_class_env/` 用同一份 `rtl/alu8.sv` 演示两条独立验证路径：

- `tb/tb_alu8.sv`：Icarus 自检 testbench（`icforge verify` 通过）。
- `verif/alu8_if.sv` + `alu8_txn.sv` + `alu8_driver.sv` + `alu8_scoreboard.sv` + `tb_alu8_env.sv`：class 化环境（`icforge svsim` 通过，本机实测 5/5 需求检查通过）。

agent 只可改 `rtl/alu8.sv`；两份 testbench 由宿主固定，其中 Icarus testbench 的 SHA-256 钉在 `project.json`。

## 复现命令

```powershell
icforge verify examples/agent_validation/sv_class_env
icforge lint   examples/agent_validation/sv_class_env
icforge svsim  examples/agent_validation/sv_class_env
```

## 边界（未做，不声称）

- 不是 UVM 库：无 factory / config_db / sequence / phase / TLM / 功能覆盖率。
- Verilator `--timing` 相对年轻；复杂时序敏感的 class 环境可能需要调参或拆分。
- 未采集代码/翻转覆盖率，也未做 post-route 或实板验证。
