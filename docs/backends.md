# 厂商中立的实现后端（阶段 2）

`icforge implement PROJECT` 把工程的 RTL 送到**目标 profile 所选的实现后端**，用一份统一、有哈希证据的 `reports/implement.json` 记录结果。这是阶段 2 的核心：实现层不再写死安路 TD，而是「数据驱动的 target profile + 后端接口 + 多后端分发」。

## 目标 profile（`fpga_agent/targets.py`）

目标以纯数据描述：厂商、器件族、工具链、约束文件约定、以及该目标**能**做什么。

| id | 厂商 | 后端 | 器件 | 能力 |
|---|---|---|---|---|
| `anlogic-hx1p35a` | Anlogic | `anlogic` | HX1P35A / PH1P35MDG324 | synthesis + place_route + bitstream |
| `lattice-ice40-hx1k` | Lattice | `ice40` | iCE40HX1K (iCEstick) | synthesis + place_route + bitstream |
| `generic` | vendor-neutral | `generic` | technology-independent | **仅 synthesis** |

- 工程在 `project.json` 里用 `"target": "lattice-ice40-hx1k"` 选择目标，缺省为 `anlogic-hx1p35a`；`icforge implement --target` 可临时覆盖。
- `"target"` 也可以是对象（`{"id": ..., "package": ..., "device": ...}`），其中的 `package`/`device` 等会覆盖 profile 默认值。
- 未知的 target id **报错**，不会静默回退到别的厂商。唯一兼容例外：`project.json` 的 `target` 若是既有的可读安路器件描述（如 `"Anlogic PH1P35MDG324"`），按缺省安路 profile 处理——因为它描述的是安路器件，不是 profile id；`--target` 显式传入的 id 仍保持严格校验。新工程模板已改用 `"target": "anlogic-hx1p35a"`。
- `capabilities` 声明目标能力。`generic` 明确声明没有 place_route/bitstream，因此 `implement` 不会为它声称位流——这是诚实契约，不是功能缺陷。

## 后端

### `ice40`（开放，许可证自由）

完整且不依赖厂商许可证的开放实现流，Windows/Linux/macOS 均可运行：

1. **综合**：Yosys `synth_ice40 -top <top> -json <netlist>`；
2. **布局布线**：`nextpnr-ice40 --hx1k --package tq144 --json ... --pcf constraints/pins.pcf --asc ...`；
3. **位流打包**：`icepack <asc> <bin>`。

工具解析与 Yosys 后端一致：`ICFORGE_NEXTPNR` / `ICFORGE_ICEPACK` 覆盖、PATH 上的原生可执行文件、或 portable 的 YosysHQ `yowasp` 入口（`pip install -e ".[openfpga]"`）。三者任一缺失即 `blocked`，**绝不伪造位流**。

- 报告记录每段的 `artifact`/`sha256`、nextpnr 的资源占用（`ICESTORM_LC`/`RAM`/`PLL`/`SB_IO`/`SB_GB`）与最大频率（`timing.max_frequency_mhz`、`constraint_mhz`、`pass`）。
- 时序来自 nextpnr 对已布线设计的静态分析，是该目标的**真实频率估算**；它不是安路 STA，也不是布线后功能仿真。

### `anlogic`（厂商）

薄适配器：调用已有的 `icforge td`（`fpga_agent.cli.do_td`），再把 `reports/td.json` 映射进统一 schema。这样安路逻辑只有一份实现，不会与已验收的 TD 路径分叉。TD 缺失、或被仿真门禁/引脚映射门禁拦下时，报告为 `blocked`。

### `generic`（厂商中立，仅综合）

复用 `icforge synth` 的 Yosys 技术无关网表。place_route 与 bitstream 两段记为 `not_run`，`reason` 明确写「该目标不提供布局布线与位流」。它回答的是「RTL 能综合成网表吗」，不是「能上板」。

## 报告与状态

`reports/implement.json`：

| 字段 | 含义 |
|---|---|
| `kind` | 固定为 `implement` |
| `backend` / `stage` | 实际后端与阶段 |
| `target` | 目标 profile 摘要（id/vendor/device/family/tool） |
| `capabilities` | 目标能力 |
| `stages.synthesis` / `place_route` / `bitstream` | 每段 `status`（`pass`/`fail`/`not_run`）、`artifact`、`sha256`、`tool` |
| `utilization` / `timing` | 后端报告的资源与时序（后端提供时） |
| `bitstream` | 位流 `path`/`sha256`/`extension` |
| `inputs` | RTL、约束文件、配置的哈希 |
| `reason` | 结论说明 |

状态语义：

| 状态 | 含义 |
|---|---|
| `pass` | 目标所**提供**的每一段都完成，且记录的产物哈希与磁盘一致 |
| `error` | 工具链齐全但某一段失败（打印 `reports/implement.log`） |
| `blocked` | 缺少目标工具链，或目标约束文件缺失，或后端被门禁拦下 |

补充约定：

- 报告绑定 RTL、约束文件与配置哈希。改动任一输入，旧报告立即失效。
- `generic` 的 `pass` **不是** bitstream；它只表示综合通过。
- `ice40` 的 `pass` 表示开放流三段都完成并打包出 `.bin`；它**不等同**实板通过。

## 审查门

`IMPL-003`（implementation，`required: false`）映射 `reports/implement.json`：

- 缺报告 → `not_run`；
- 工具缺失/约束缺失 → `blocked`（附原因）；
- 后端失败 → `blocked`（附原因）；
- 当前输入上通过 → `pass`。

它是可选门，不阻断 `artifact_ready`。交付路径现在支持两条：非安路、有 `bitstream` 能力的目标经 `DELIV-001` 记厂商中立交付证据，`artifact_ready` 的 `fpga` 范围可由安路 TD 证据（`IMPL-001`）**或**开放后端位流证据满足；`package` 会打包 `.bin` 并在 `delivery.json` 写 `vendor_neutral_bitstream`。位流仍是真实产物但未做布线后仿真与实板验证。细节见 [coverage.md](coverage.md) 与 `docs/engineering-workflow.md` 的交付一致性一节。

## 夹具

`examples/agent_validation/ice40_pwm/`：一份 `rtl/pwm_breath.v`（自由计数器驱动 PWM 占空比与电平 nibble），走完整开放流，本机实测通过：

- `verify`：Icarus 自检 3/3 CHECK；
- `lint`：Verilator `-Wall` 通过；
- `synth`：73 单元 / 13 触发器；
- `lec`：13 个等价点全证；
- `implement`：synth_ice40 → nextpnr-ice40 → icepack，25 LC，`clk` 时序 PASS（12 MHz 约束），产出 `.bin`。

`constraints/pins.pcf` 是 iCEstick 的夹具引脚映射，**不是**已核实的板级电气约束。

## 复现命令

```powershell
icforge targets                          # 列出内置目标
icforge verify examples/agent_validation/ice40_pwm
icforge lint   examples/agent_validation/ice40_pwm
icforge synth  examples/agent_validation/ice40_pwm
icforge lec    examples/agent_validation/ice40_pwm
icforge implement examples/agent_validation/ice40_pwm
icforge coverage examples/agent_validation/ice40_pwm   # RTL 结构覆盖率（阶段 3）
```

## 边界（未做，不声称）

- `ice40` 后端只实现 hx1k 这一族；ECP5、MachXO2 等族未接入。
- 位流未做布线后仿真、未下载到板、未做实板验证；`timing` 是静态估算。
- `anlogic` 后端仍依赖 Windows 上的 TD 安装与许可证。
- `DELIV-001` 只覆盖有 `bitstream` 能力的目标；`generic`（仅综合）不声称交付。交付包不包含布线后仿真或实板证据，结构覆盖（`COV-001`）也不构成实板通过。
