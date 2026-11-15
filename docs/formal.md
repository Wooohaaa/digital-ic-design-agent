# 形式验证与逻辑等价（LEC）

IC Forge 提供两个独立的证据门禁，都由 Yosys 驱动：

- `icforge lec`：逻辑等价检查（Logic Equivalence Check）。证明 RTL 与其综合出的技术无关门级网表等价，或证明两份 HDL 设计等价。
- `icforge formal`：属性检查。用有界模型检测（BMC）或时序归纳（temporal induction）证明 RTL 中声明的断言。

两者都不使用 SymbiYosys：SymbiYosys 的 `yowasp-sby` Windows/WASM 运行时会在 `sby_core.py` 抛出 `UnicodeDecodeError`，因此本流程直接调用 Yosys 的 `sat` 与 `equiv_*` 命令。

## 后端与安装

后端解析顺序：

1. 环境变量 `ICFORGE_YOSYS` 指向的可执行文件；
2. `PATH` 上的原生 `yosys`；
3. 项目自带的 `.tools/yosys/bin`；
4. 当前 Python 环境中的 `yowasp-yosys` 入口点（可移植 WASM 构建）。

推荐安装可移植后端：

```powershell
pip install -e ".[formal]"
```

缺少任何后端时，这两个命令不会假装成功：它们写 `status: blocked` 报告并返回退出码 2。工具版本会记入报告（`tool_version`）。

## 逻辑等价 `icforge lec`

```powershell
icforge lec examples/agent_validation/sync_fifo
icforge lec my-project --mode designs --gold rtl/sync_fifo.v --gate rtl/sync_fifo_revised.v
icforge lec my-project --mode designs --top sync_fifo --timeout 600
```

- `--mode synth`（默认）：读取项目 RTL，归一化后综合出技术无关门级网表并写出 `build/lec/<top>_netlist.v`，然后证明 RTL 与该网表等价。这是“RTL 与它自己的门级表示一致”的证据。
- `--mode designs`：证明两份 HDL 源文件集合等价（`--gold` 参考 vs. `--gate` 实现），用于证明一次 RTL 修订没有改变行为。

两侧使用相同的归一化流程：`proc; opt; memory; opt; async2sync`，门级侧再加 `techmap; opt`。`async2sync` 把异步复位降到保守的同步模型，使 SAT 能推理等价点；因为两侧共享同一复位模型，比较在该模型下保持可靠。等价比较使用 `equiv_make`、`equiv_simple`、`equiv_induct`、`equiv_status -assert`。

报告 `reports/lec.json` 记录：状态、模式、顶层、归一化流程、逐项 `proven`/`unproven` 等价单元计数、未证点名称、写出的网表路径与 SHA-256、工具版本、输入哈希与耗时。`reports/lec.log` 保存原始 Yosys 输出。

**诚实性约定**：只要有等价单元未证明，状态就是 `fail`，绝不会读作“等价”。`lec_valid`（工程审查中的 `DV-006`）还会要求报告的输入哈希与当前 RTL 一致、且 `proven ≥ 1`、`unproven == 0`，因此改动 RTL 会让旧的等价证据失效。

## 属性检查 `icforge formal`

```powershell
icforge formal my-project                       # BMC，深度取 manifest 或默认 10
icforge formal my-project --mode prove          # 时序归纳（无界）
icforge formal my-project --depth 32
icforge formal my-project --no-init-zero        # 从完全不受约束的初态检查
```

流程：`read_verilog -formal -sv` → `hierarchy` → `proc; opt; memory; opt` →（可选）`setundef -init -zero` → `clk2fflogic` → `chformal -lower` → `sat -prove-asserts -verify`，`bmc` 模式加 `-seq <steps>`，`prove` 模式用 `-tempinduct`。

`clk2fflogic` 会把时钟变成普通信号，因此一个 SAT step 不是一个时钟周期：一个上升沿约需 2 个 step，另加固定的启动开销。脚本据此把 `--depth` 的**时钟周期数**换算为 `steps = 2 * depth + 2`，保证至少探索 `depth` 个时钟周期（报告同时记录 `depth` 与 `steps`）。

- `--mode bmc`（默认）：在 `depth` 个**时钟周期**内检查。报告记 `bounded: true`，理由文本明确写“holds for at least N clock cycles (bounded)”。有界通过绝不被当作证明。
- `--mode prove`：时序归纳。只有 `sat` 报告 `Induction step proven: SUCCESS!` 时才记 `pass`，理由写“unbounded”。归纳步找到模型或基例出现反例都记 `fail`。

### 初态约定

默认 `set_init_zero = true`：假设所有触发器上电归零，这是 FPGA 厂商的默认约定，也是真实硬件的上电行为。它通过 `setundef -init -zero` 在 `clk2fflogic` **之前**应用。

不要使用 `sat -set-init-zero` 配合 `clk2fflogic`：该标志会同时把 `clk2fflogic` 注入的时钟仿真触发器清零，从而掩盖反例（一个可伪造的断言会错误地报 `SUCCESS`）。`setundef -init -zero` 没有这个问题。

用 `--no-init-zero`（或 manifest `formal.set_init_zero: false`）从完全不受约束的初态检查，此时断言必须在任意初态下都成立才能通过——这是最强的可靠主张，但带同步复位的时序属性通常会在第 0 周期失败。

声明了零个断言的工程得到 `not_applicable`，绝不是 `pass`。`memory` 映射是必需的：Yosys 的 `sat` 无法处理未展开的 `$memwr_v2`/`$memrd` 单元。

报告 `reports/formal.json` 记录：模式、是否 `bounded`、深度、`set_init_zero`、被检查的断言数、工具版本、输入哈希与耗时；`reports/formal.log` 保存原始 SAT 输出。`formal_valid`（`DV-007`）要求 `status == pass`、输入哈希与当前 RTL 一致、且至少覆盖一个断言。

## Manifest 配置

```json
{
  "top_module": "sync_fifo",
  "rtl_sources": ["rtl/sync_fifo.v"],
  "lec": {
    "mode": "designs",
    "top": "sync_fifo",
    "gold_sources": ["rtl/sync_fifo.v"],
    "gate_sources": ["rtl/sync_fifo_revised.v"]
  },
  "formal": {
    "mode": "bmc",
    "top": "sync_fifo",
    "depth": 16,
    "set_init_zero": true,
    "sources": ["rtl/sync_fifo.v"]
  }
}
```

命令行参数优先于 manifest。`lec.gold_sources` 缺省时回退到 `rtl_sources`；`formal.sources` 缺省时回退到 `rtl_sources`。

## 工程审查门禁

`icforge review` 把报告映射为两个可选（`required: false`）门禁：

- `DV-006` 逻辑等价：通过需当前输入上的完整等价证明。
- `DV-007` 形式属性：通过需当前输入上的属性证明；无断言记 `not_run`；反例记 `blocked`。

它们是可选门禁，不会阻断 `artifact_ready`，但会随工程审查报告和交付包归档。覆盖闭合（`DV-004`）与 mutation 强度（`DV-005`）仍然是独立事项。

## 退出码

两个命令：`0` 通过，`1` 失败（反例或不完整等价），`2` 被阻塞/不适用/出错（例如缺少 Yosys）。

## 参考来源

- [Yosys 文档](https://yosyshq.readthedocs.io/)：`equiv_*`、`sat`、`clk2fflogic`、`chformal`、`memory`、`setundef` 命令语义。
- [YosysHQ/yowasp](https://github.com/YosysHQ/yowasp)：可移植 WASM 构建，作为无需原生安装的后端。
- [SymbiYosys 参考](https://github.com/YosysHQ/sby/blob/main/docs/source/reference.rst)：本流程借鉴其分别记录 prove/bmc/cover、假设和深度、不把有界通过写成无界证明的做法，但不使用其运行时。
