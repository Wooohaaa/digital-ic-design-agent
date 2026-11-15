# RTL 结构与功能覆盖率（阶段 3 / 阶段 5）

`icforge coverage PROJECT` 用 Verilator `--binary --coverage` 构建并运行自检 testbench，再把 SystemC 的 `coverage.dat` 解析成**逐 RTL 源文件、逐点**的结构覆盖率，写入 `reports/coverage.json`。默认只测结构（line / branch / toggle / expr）；加上 `--functional-coverage`（或 manifest `coverage.functional_coverage: true`）时，同时收集 `cover property` 的**功能覆盖点**并检查并发断言是否违例。

它回答两个有哈希证据的问题：**(1) 这份 RTL 的每个结构点是否都被当前 testbench 至少触发过一次？(2) 声明的 `cover property` 是否都被命中、`assert property` 是否违例？** 它不回答「所有功能都被验证」或「规格已覆盖」。

## 为什么需要单独一条路径

主仿真门禁是 Icarus 自检（`icforge verify`），它只报告 PASS/FAIL，不给结构覆盖。Verilator 的 `--coverage` 会插入覆盖点并写出 SystemC 格式的 `coverage.dat`，因此 `coverage` 复用 Verilator 的编译与运行管线（`fpga_agent.svsim` 的工具链解析、`_run`）。

默认配置下它是**结构**覆盖：

- line：某行源码是否执行；
- branch：某分支是否双向执行；
- toggle：某信号是否发生 `0->1` 与 `1->0` 翻转（Verilator 默认 `--coverage-toggle`）；
- expr：表达式内各子项是否被取值。

它**不等于**功能覆盖、断言覆盖或规格覆盖。默认 `pass` 只说明结构点被触发；加上 `--functional-coverage` 后 `pass` 才同时要求 `cover property` 命中与无断言违例。

## manifest 配置

覆盖计划从 `project.json` 解析，也可由命令行覆盖：

- RTL 顶层：`manifest.top_module`（或 `--top`）；
- RTL 源：`manifest.rtl_sources`；
- testbench 顶层：`manifest.testbench_top`（或 `--testbench-top`）；
- testbench 源：`manifest.testbench_sources`；
- 可选 `manifest.coverage.sources`：把**测量**范围收窄到子集（编译仍用 `rtl_sources`）——用于整合夹具只测自己贡献的逻辑，同时编译复用 IP（如自带验证套件的 CPU 核）；缺省时测量范围等于编译范围；
- 可选 `manifest.coverage.cflags`：传给 Verilator `-CFLAGS` 的优化开关，默认 `-O2`；
- 可选 `manifest.coverage.functional_coverage`：请求 `cover property` 功能覆盖点（等价于命令行 `--functional-coverage`）；
- 可选 `manifest.coverage.unreachable`：把**可证不可达**（而非仅未激励）的点显式豁免。每项是 `{"object": "<点对象名>", "reason": "<理由>", "line": <可选>}`。匹配到的点从分母剔除、记录在报告的 `waived_unreachable`（附理由），**绝不计为覆盖**；未匹配任何点的声明记录在 `waiver_unmatched`，防止豁免声明因笔误/过时而静默失效。豁免声明参与输入 hash，改动即令旧报告失效重跑。

任一必填项缺失时命令报错，不会猜。testbench 源必须存在且可编译——没有 testbench 就没有激励，覆盖无从谈起。

## 工具链要求

与 `icforge svsim` 相同：Verilator + C++ 编译器 + make。三者任一缺失时命令报 `blocked` 并给出原因，**绝不报 pass**。

## 构建参数与警告抑制

覆盖构建使用：

```text
verilator --binary --timing --coverage --top-module <tb_top> \
  -Mdir build/coverage/obj -o sim -CFLAGS -O2 -I <project> \
  -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-PROCASSINIT -Wno-DECLFILENAME \
  <rtl sources> <testbench sources>
```

- `--coverage` 在 RTL 结构点插入探针，运行结束在**项目根 cwd** 写出 `coverage.dat`（不在 objdir）。
- 覆盖构建**不叠加 `-Wall`**：testbench 是宿主编写的 Icarus 风格测试台，不是 Verilator-lint 干净代码，`-Wall` 会因宽度/声明等噪声拒绝构建。严格 lint 仍是对 RTL 的独立门禁（`icforge lint` / `RTL-001`）。这里只抑制实测确实存在、且与覆盖无关的警告（`-Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-PROCASSINIT -Wno-DECLFILENAME`）；`-Wno-MULTIINDEX` 在 5.048 不存在，加上会报错，故不用。
- `--timing` 让 testbench 里的 `#delay`/时钟驱动在 Verilator 下可运行。

## coverage.dat 解析

记录格式为 `C '<key>\x02<value>\x01<key>\x02<value>...`，关键字段：

| 键 | 含义 |
|---|---|
| `f` | 源文件绝对路径 |
| `l` | 行号 |
| `t` | 点类型：`line` / `branch` / `toggle` / `expr`（结构）或 `user`（功能 `cover property`） |
| `o` | 对象（如 `q:0->1`、`block`） |
| `h` | 命中次数，形如 `"<scope>' <n>"`，取最后一个空格后的整数 |

解析器把点按 `basename` 归到声明的 RTL 源上，testbench 自身的点不计入 RTL 覆盖。**没有任何点**命中的 RTL 源记 `blocked`，不会被读成已覆盖。

`coverage.dat` 里功能覆盖点的类型是 **`user`**、对象形如 `cover|S<line>`，命中次数即 `cover property` 采样为真的次数。结构汇总会**排除**这些 `user` 点，功能汇总只取 `user` 点，两者互不污染。

## 功能覆盖与并发断言（阶段 5）

加上 `--functional-coverage`（或在 `project.json` 设 `"coverage": {"functional_coverage": true}`）时：

- RTL 里的 `cover property (@(posedge clk) <cond>)` 各变成一个功能覆盖点；汇总按 RTL 源聚合，命中数为 0 的点列进 `functional.uncovered_points`（附源、行号、对象）。
- RTL 里的 `assert property (...)` 是**并发断言**：Verilator 5.048 支持模块级并发断言，违例会非零退出并在输出里打印 `%Error: ... Assertion failed`。只要出现即判 `fail`，报告里记 `assertion_failure`。
- 注意 Verilator 对时钟边沿上的状态断言**在 NBA 之后采样**：例如 `en |-> cnt != 15`，即使 testbench 在 `cnt` 到 15 前把 `en` 拉低，`cnt` 已在 15 的边沿仍会触发。因此功能覆盖夹具改用复位行为断言 `!rst_n |-> cnt == 0`，与 enable/count 采样顺序无关。
- 请求了功能覆盖但 RTL 没有 `cover property` 点时记 `blocked`（无法满足请求），不是裸 `pass`。

**`pass` 的含义**：结构覆盖完整 **且** 每个声明的 `cover property` 都被命中、无并发断言违例。它是功能**点**覆盖，不是断言覆盖率（多少条断言被触发），也不是「所有功能都被验证」。

## 报告与状态

`reports/coverage.json` 记录状态、顶层、源列表、工具版本、C++/make 路径、命令、逐源逐类型的结构点计数、结构覆盖百分比、功能段、断言违例、输入哈希与 `reason`。状态语义：

| 状态 | 含义 |
|---|---|
| `pass` | 构建成功、testbench 干净跑完（`PASS` 行、无 `FAIL`/`FATAL`/`ERROR`）、无断言违例，且结构覆盖完整；请求功能覆盖时**还要**每个 `cover property` 都被命中 |
| `fail` | 运行非零退出、超时、出现失败行，或并发断言违例 |
| `error` | 工具链齐全但 Verilator 构建失败 |
| `blocked` | 缺少工具链，或构建/运行通过但结构覆盖不完整（附未覆盖点）；请求功能覆盖却无 cover 点时也记 `blocked` |

报告另含 `structural_status` 与 `functional_status` 两段（`pass`/`fail`/`blocked`/`not_run`），整体 `status` 由结构优先、再按是否请求功能覆盖综合。

补充约定：

- 报告绑定 RTL、testbench、`spec/` 规格与验证计划、以及覆盖配置（顶层/源/cflags/**functional_coverage**）的哈希。改动任一输入，旧报告立即失效。
- `pass` 是**当前输入上的结构（及请求时的功能点）覆盖**，不代表功能完备或签核。
- 覆盖百分比是 `covered / points`，点数由 Verilator 决定，随 RTL 结构变化，不是固定预算。

## 审查门

`COV-001`（verification，`required: false`）映射**结构**覆盖：缺报告记 `not_run`，工具缺失记 `blocked`，失败记 `blocked`（附原因），当前输入上的完整结构覆盖记 `pass`。它是可选门，不阻断 `artifact_ready`。

`COV-002`（verification，`required: false`）映射**功能**覆盖：缺 `functional` 段记 `not_run`（除非请求过功能覆盖则记 `blocked`），断言违例或覆盖不完整记 `blocked`，完整覆盖记 `pass`。它与 `COV-001` 分开，避免「结构点全命中」被读成「功能点全命中」。

`DELIV-001`（integration，`required: false`）反映厂商中立交付证据：当所选 target 是非安路且有 `bitstream` 能力、且当前 `reports/implement.json` 有效时记 `pass`。它是可选门，用于把开放后端接入交付签核路径；`artifact_ready` 的 `fpga` 范围据此可由 `valid_td` **或** `neutral_deliverable` 满足。

## 夹具

- `examples/agent_validation/ice40_pwm/`：用 `tb/tb_pwm_breath.v` 驱动 `rtl/pwm_breath.v`。本机实测：62/62 RTL 结构点全部覆盖（toggle 46、line 6、branch 4、expr 6，100.0%）。覆盖报告与实现报告一起进入交付包。
- `examples/agent_validation/functional_cov/`：阶段 5 功能覆盖夹具。`rtl/counter.sv` 是 4-bit 计数器，带 4 个 `cover property`（`cnt==0/5/15`、`en==1`）与 1 条 `assert property (!rst_n |-> cnt==0)`；`tb/tb_counter.sv` 由宿主固定并钉 SHA-256。本机实测：结构 20/20、功能 4/4、`assertion_failure=None`、`status=pass`。

agent 只可改 `rtl/`；testbench 由宿主固定，其 SHA-256 钉在 `project.json`。

## 复现命令

```powershell
icforge coverage examples/agent_validation/ice40_pwm
icforge coverage examples/agent_validation/functional_cov --functional-coverage
```

## 边界（未做，不声称）

- 这是**结构（及请求时的功能点）覆盖**，不是功能完备性、断言覆盖率或规格覆盖；`pass` 不证明所有行为被验证。
- 功能覆盖只统计声明的 `cover property` 是否命中，不统计断言**覆盖率**（多少条断言被触发）。
- `coverage.unreachable` 是**显式、可审计的有界声明**：被豁免的点未被任何激励命中，理由写在报告里；它从分母剔除但绝不计为覆盖，也不是"已覆盖"。豁免必须逐点给理由，未匹配的声明会被报告出来。
- 只统计声明的 RTL 源；testbench 自身点不计入。
- 未做 post-route 仿真、未下载到板、未做实板验证；iCE40 布线后仿真（`icesim` 等）在当前环境不可得（无 `yowasp-icesim` 包），明确未做（见 `docs/timing.md`）。
- 不引入覆盖率闭合（coverage closure）或自动补激励；未覆盖点由 `blocked` 报告列出，人工/模型扩展 testbench 后再重跑。
- 结构点数随 RTL 变化，百分比不可跨工程直接比较。
