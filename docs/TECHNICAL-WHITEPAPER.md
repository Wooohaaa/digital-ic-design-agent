# IC Forge 技术白皮书

> 面向数字 IC / FPGA 工程的终端 Agent：从自然语言需求到可复现证据链的完整工程闭环。
>
> 本文聚焦三件事：**它做出了什么效果**、**它坚持什么理念**、**它用什么技术实现**。
> 所有数字均来自仓库内已保存的报告或本机实测；未关闭项按原样列出，不把局部结果升级为工程签核。

---

## 0. 一句话定位

IC Forge 不是一个"帮你写 Verilog 的聊天机器人"，而是一个**持有独立验收权的工程 Agent**：
它生成设计，但**不持有修改验收标准的权限**；每个结论都必须附带可重放、可指纹绑定的证据；
"未证明"、"工具缺失"、"超时" 都是一等公民状态，不会被包装成成功。

它同时是一个**多 Agent 编排器**：可把有明确文件范围的工作派发给本机 Codex、Claude Code、
OpenCode，在隔离 worktree 中执行，再由宿主工程门禁统一验收。

代码规模：13,407 行 Python（`fpga_agent/`），38 个测试文件、217 个测试函数，
CI 矩阵为 Python 3.11/3.12 × Windows/Linux 全绿。

---

## 1. 先进理念：五条不可让步的工程原则

这些原则不是口号，而是直接体现在代码结构、退出码和门禁逻辑中。

### 1.1 设计者与验证者分离

> Agent 生成设计，验证方持有独立规格、参考模型、测试与门禁；设计修复不能自行修改验收标准。

这是整个项目最核心的架构决策。在 `docs/flow.md` 中明确规定：

- 普通 RTL 模块：模型只写 `rtl/`、`tb/`、`spec/design.md`；**验证目标从需求导出并冻结在 `project.json`**。
- CPU：模型**只生成 RTL**；独立 Python ISA 参考模型、受保护 testbench、退休/存储/内存/SoC 签名检查全部由验证侧固定。
- 自动修复循环最多 5 轮，且**冻结已接受的 testbench 与验收目标**——修复只能改 RTL，不能改尺子。

这解决了 LLM 参与工程时最典型失效模式：**为了让测试通过而悄悄改测试**。

### 1.2 证据链优先于声明

每个候选版本都必须保留完整链条：

```
需求 → 实现 → 仿真/形式证据 → 综合/物理证据 → 指标 → 接受或回退
```

形式化后为：

- 输入与配置绑定 SHA-256，复制到独立 `build/lec/<run-id>/inputs/`
- 报告记录状态、命令、工具版本、证明点、产物指纹
- `lec_valid()` 重验输入、checker、所有必要产物和证明清单
- 改源码、include、配置、脚本或产物 → **旧证据自动失效**

"一次生成成功、自测 PASS 或增加代码量" 在路线图中被明确定义为**不构成闭环**。

### 1.3 失败是一等状态，不是需要隐藏的异常

LEC 的返回码被严格区分：`0 = pass`、`2 = 工具缺失 blocked`、`1 = 失败/超时/未证明/输入错误`。
路线图进一步规定：`timeout`、`unsupported`、`unproven` **均不能计为通过**。

在硬件/交付门禁中，`artifact_ready` 与 `engineering_signoff` 是两个不同字段；
即便位流已生成、STA WNS 为正，只要约束未闭合，`engineering_signoff` 仍为 `not_granted`。
这种"诚实降级"在 AI 工程中非常罕见，却是可信自动化的前提。

### 1.4 冻结契约下的可量化进化

`docs/rsi-roadmap.md` 定义：只有在**独立验收和固定预算下得到可重复改进**，才计作进化。
每轮实验固定 workload、验证器版本、模型版本/输入、搜索预算和工具环境；
保留基线与候选、失败原因、质量/性能/资源变化及人工干预次数；
**留出从未用于候选修复的评估集**；改动验证器则单独审阅并重建证据，不沿用旧分数。

### 1.5 人机边界显式化

人提供方向、现实资源边界、签核、PDK、设备、生产与物理操作；
Agent 不宣称越过这些边界。例如：

- TD 是 Windows 专用工具，未纳入 GitHub 托管 runner
- Agent 当前**不控制下载器、不写 Flash**；实板编程由用户操作
- PDK、工艺、封装、DFT、硅后测试均列为 Stage 3 剩余依赖，而非已实现能力

---

## 2. 系统架构与技术组成

### 2.1 分层结构

```
┌─────────────────────────────────────────────────────────────┐
│  交互层    Textual TUI · CLI (icforge) · 受限工具集           │
├─────────────────────────────────────────────────────────────┤
│  认知层    Planner · 上下文预算/压缩 · 技能发现 · 历史回忆     │
│            (context_budget.py / knowledge.py / task_planner.py)│
├─────────────────────────────────────────────────────────────┤
│  编排层    持久任务 · 后台 worker · worktree 隔离 · 多 Agent   │
│            (autonomy.py / orchestration.py / agent_backends.py)│
├─────────────────────────────────────────────────────────────┤
│  工程层    需求/设计 · 仿真 · lint · LEC/形式证明 · CPU 验证   │
│            (project.py / riscv.py / lec.py / spec_proof.py /  │
│             cpu_external.py / cpu_act.py / cpu_stage2.py /   │
│             cpu_rv64.py / sv39_accept.py)                    │
├─────────────────────────────────────────────────────────────┤
│  实现层    Anlogic TD 适配 · 位流/交付打包 · 硬件证据          │
│            (td.py / programmer.py / hardware_evidence.py)    │
├─────────────────────────────────────────────────────────────┤
│  门禁层    工程审查 · artifact readiness · engineering_signoff │
│            (quality.py / spec_gate.py / evidence.py)         │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 关键技术一：三层形式等价验证体系

`fpga_agent/lec.py` + `lec_state.py` + `lec_reset.py`（reset-state-relation）构成三层证明：

| 方法 | 证明内容 | 适用 |
|---|---|---|
| `direct` | `equiv_make` → `equiv_simple -undef` → `equiv_status -assert`，要求每个 `$equiv` 的 A/B 已合并、有可观察输出、证明点非空 | 组合逻辑 |
| `state-bijection` | 完整状态双射 + 初态集合 + 异步复位事件 + 全部下一状态/输出义务的 SAT 证明 | 状态保持型时序设计 |
| `reset-state-relation` | 显式复位后状态关系，支持状态合并（schema 3） | 复位后收敛的多周期设计 |

关键取舍：

- **不使用 `equiv_induct` 弱时序归纳**，不开启忽略未知单元或环境假设
- golden 与 synthesis 共享 Yosys 前端，因此**不声称证明前端 SV 语义正确**
- `-undef` 按 Yosys 模型处理，不解释为完整四态 SV 仿真一致性
- 外部网表仅支持 Yosys 可理解的 Verilog 单元，未知技术单元不会通过

这使 LEC 成为**门禁**而非装饰：配置 `"formal_equivalence": {"required": true}` 后，
它直接影响 artifact readiness、长期任务完成判断和交付打包。

### 2.3 关键技术二：独立 ISA 参考 + 差分 + 官方签名

CPU 验证链路由四层独立机制组成：

1. **独立 Python ISA 参考模型**：不复用 RTL 实现，逐指令比对 PC、指令、目的寄存器与写入值
2. **Unicorn 外部参考差分**：比对每次退休、每笔 store、最终 RAM
3. **RISC-V ACT 官方架构测试**：固定版本 `riscv-arch-test@96e3589a`，编译原始源码，逐签名字比对
4. **EEI 契约冻结**：RV32I 2.1 终止型 EEI、RV64I 2.1 小端单 hart，终止条件写入契约

变异测试被明确要求：检查器必须能找到注入的错误，否则不算有效验证。

### 2.4 关键技术三：证据指纹与失效传播

`fpga_agent/evidence.py` 实现基于 SHA-256 的证据绑定：

- 输入、配置、checker、脚本、产物全部指纹化
- 任一环节变化 → 依赖它的上层证据失效
- 交付 ZIP 携带原始报告、网表、脚本、快照、日志，打包前后复核字节指纹
- 从 ZIP 解包后可重新执行门禁（"交付还原"测试覆盖此路径）

这解决了 AI 生成工程中最隐蔽的问题：**证据与代码悄悄脱节**。

### 2.5 关键技术四：跨 Agent 编排与固定宿主验收

`orchestration.py` 的设计要点：

- 主管模型**只读规划**；执行者仅在自己的 worktree 内获得实现权限
- 支持本机 Codex / Claude Code / OpenCode，各自使用原生账号与配置
- 主管凭据**不自动传给执行者**；需显式 `ICFORGE_EXECUTOR_` 前缀覆盖
- 通用任务要求预先声明 `.icforge/checks.json`；**主管不能在任务过程中替换验收配置**
- 没有配置文件但存在 `tests/` 时默认运行 Python unittest；其它工程必须显式提供检查

即：**Agent 可以分工，但验收权始终在宿主**。

### 2.6 关键技术五：有界上下文与可迁移流程知识

借鉴 OpenCode / Hermes / Command Code 的公开机制后独立实现：

- 默认 96,000 字符输入预算，预留 8,000 字符空间；分阶段裁剪可重读数据
- 保护用户目标、追加要求与已加载技能；核心内容超预算时**明确阻塞**
- 裁剪内容保存至 `context-full.json`，每步日志独立留存，历史可通过引用分页找回
- 技能目录兼容 `.icforge`、`.agents`、`.opencode`、`.claude`、`.commandcode`、`.hermes`
- 同名技能不静默覆盖；外部技能附带脚本**不会自动执行**
- `recall` 结果带 `historical_only` 标记，历史成功不会被误当成当前成功

---

## 3. 效果：已验证的成果

### 3.1 软件与 CI

| 指标 | 结果 |
|---|---|
| CI 矩阵 | Python 3.11/3.12 × Windows/Linux 全绿 |
| HDL 夹具 | Icarus + Verilator 对 `sync_fifo` / `spi_master` / `apb_pwm_timer` 三个固定夹具仿真与严格 lint 通过 |
| 测试规模 | 38 个测试文件、217 个测试函数 |
| 历史基线回归 | 204 passed / 1 skipped（Windows symlink 权限）；本机覆盖率 67% |
| 编排模块覆盖率 | `orchestration.py` 86%、`task_planner.py` 86%、`agent_backends.py` 87% |

### 3.2 RV32I 三种微架构（同工作集）

固定 114 个程序、18,099 条退休指令、64 个确定性随机种子：

| 结构 | 综合 CPI | 50 MHz 折算 MIPS | LUT6 | 寄存器 | Setup/hold WNS | STA 覆盖率 |
|---|---:|---:|---:|---:|---:|---:|
| 单周期 | 1.0009 | 49.96 | 2004 | 2529 | 0.463 / 0.073 ns | 92.36% |
| 多周期 | 3.0018 | 16.66 | 2727 | 3532 | 7.284 / 0.034 ns | 92.24% |
| 两级流水线 | 1.1290 | 44.29 | 2837 | 3333 | 0.696 / 0.039 ns | 92.85% |

所有最终 TNS 均为 0。单周期核已完成 HX1P35A SRAM-only JTAG 配置
（IDCODE `4808f0e7`，位流 SHA-256 `83ea9b12…e51f7`，OpenOCD 严格 TDO 零错误）。

### 3.3 形式验证与独立架构测试

| 项目 | 结果 |
|---|---|
| 加法器穷举 | 131,072 种输入全部通过；9 个等价点直接 SAT 通过 |
| 组合 LEC 反例 | 错误加/减网表被稳定拒绝 |
| 时序 LEC | 单周期 1,128 位、两级流水 1,193 位状态完成强状态双射证明 |
| 复位后契约 | 多周期核 1,336→1,334 位合并状态在复位后契约下通过 |
| RV32I ACT | 官方 ACT 2.7.4 的 38 个用例全部通过 |
| RV64I ACT | 50 项上游 RV64I 签名通过 |
| 规格门禁 | 复位错误的 RTL 可通过自身仿真与自网表 LEC，但被规格 SAT 反例阻止交付 |

### 3.4 Stage 3 RV64 工程基线

- RV64I 2.1：**30 条整型计算指令**，六种条件分支、JAL/JALR、七种 load、四种 store、FENCE
- 机器态运行时：`mtvec`/`mepc`/`mcause`/`mtval`/`mscratch` 与 MIE/MPIE，六种 CSR 形式与 MRET
- 独立 **Sv39 三级页表遍历器**：规范地址、4 KiB/大页、SUM/MXR、Svade A/D、非法 PTE 与访问错误
- 裸机 LLVM 编译 C checksum 通过 LP64 调用约定启动
- 物理基线：ECP5 LFE5U-85F CABGA381、20 MHz、固定种子 7 的 out-of-context nextpnr 路由

### 3.5 实板证据

- **HDMI 旋转立方体**：两次独立 SRAM 重载，采集卡分别接受 18/18 与 30/30 个视频样本，均识别多个旋转姿态；setup/hold WNS +5.746/+0.066 ns，TNS = 0
- **TinyStories260K**：模型完整性 CRC、logits 与生成 token 有板上验收记录

---

## 4. 边界与未关闭事项（同样属于技术成果）

项目的成熟度恰恰体现在**清楚知道自己没做到什么**：

- `engineering_signoff` 仍为 `not_granted`
- CPU STA 未覆盖端点、IO delay、时钟/复位例外；R11 串行签名未外部采集
- 多时钟、重定时/重编码、工艺库与完整 SoC 的 LEC 未覆盖
- 尚未实现完整 SV/UVM 语义与自研后端
- 无 ASIC 标准单元库/Liberty mapping、STA、CDC/RDC、DFT、功耗、DRC/LVS、封装与硅后测试
- Stage 4–7（tiny GPU、LPU、数据中心、全计算体系）保留各自退出条件，未宣称完成
- Python agent 总覆盖率 67%，部分错误分支仍待补测

---

## 5. 复现入口

```powershell
uv sync --locked --all-extras
uv run --locked --all-extras --python 3.11 pytest --cov=fpga_agent --cov-report=term-missing
uv run --locked python -m fpga_agent cpu-suite
uv run --locked --with yowasp-yosys==0.71.0.0.post1285 python -m fpga_agent lec examples/lec/adder
```

Stage 3 完整复现（含 ACT、Sv39、ECP5）见 [`rva23-stage3.md`](rva23-stage3.md)；
七阶段目标与退出条件见 [`rsi-roadmap.md`](rsi-roadmap.md)；
逐项工程门禁见 [`engineering-workflow.md`](engineering-workflow.md)。

---

## 6. 总结

IC Forge 的技术价值不在"生成 RTL 的能力"，而在**把 LLM 生成能力约束进可验证、可回退、可审计的工程闭环**：

1. **独立性**——设计者与验证者分离，尺子不由被考核者持有；
2. **可证伪性**——每个结论绑定输入指纹与可重放证据，证据会随代码失效；
3. **诚实性**——unproven / blocked / not_granted 是一等状态，不用成功幻觉换取表面指标；
4. **可迁移性**——流程知识与多 Agent 编排跨工具链复用，但验收权始终在宿主；
5. **长期性**——以冻结预算和留出评估集衡量"进化"，而非一次生成的高光。

这是一套面向 AI 时代硬件工程的**治理架构**，而非单一功能工具。
