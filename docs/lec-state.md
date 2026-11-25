# 时序 LEC：状态双射与归纳不变量

2026-11-23：在含顶层与子模块的计数器工程复验状态双射。仅在规范化模型中去掉 Yosys 展平生成、无引脚连接的 `$scopeinfo` 层级元数据；有连接的未知单元仍拒绝。

日期：2026-11-17。实现：`fpga_agent/lec_state.py`；使用 `formal_equivalence.method = "state-bijection"` 显式选择。
第三轮补充：原双射契约不变；新增合并状态的[复位后契约](lec-reset.md)，schema 当前为 3，历史 schema 2 报告需重新核验/生成。
本方法为具有完整对应状态位的设计提供无周期上界的状态/输出等价检查，不使用 `equiv_induct` 的弱等价定义或“仿真若干周期后认为同步”的推断。

## 可检查的证明契约

golden 与 gate 都展开为一个模块，存储器降为触发器/组合逻辑。时钟使能和同步复位通过 `dffunmap` 还原到 D 输入的 mux；禁用读寄存器吸收与 FSM 重编码，避免暗中改变本方法要求的状态编码。

令两个设计分别具有状态 s/t、初态集合 I、时钟下一状态函数 T、输出函数 O。检查器建立逐位双射 phi，逐项检查：

1. **完整性**：所有状态位均有且仅有对应位；不能只选容易证明的输出或状态子集。公开寄存器名、索引和别名只用于提出映射；它们不能替代下面的证明。
2. **初态**：`I_gold(s)` 与 `I_gate(phi(s))` 相同。保留显式初始化位；未初始化位表示自由 Boolean 初值，不自动设零，也不假设一定先复位。
3. **时钟迁移**：对所有对应状态和输入，`phi(T_gold(s,u)) == T_gate(phi(s),u)`。把两边 Q 替换为共同状态输入，将所有 D 及异步复位覆盖值一起交给 SAT 检查。
4. **输出**：对相同的全部输入和对应状态，所有外部输出相同。
5. **异步复位事件**：对应状态位必须具有相同的主输入复位信号、极性和复位值（或两边均无异步复位）。因此每次异步复位断言也保持关系；时钟沿上的复位优先级另进入第 3 项。

初态满足关系，每个时钟/复位事件保持关系，输出在关系下相同，因而可按事件数归纳得出任意周期的对应轨迹相同。SAT 比较覆盖所有 Boolean 状态，包括不可达状态；没有依赖测试程序或可达性假设。
这里的含义是两边允许的初态集合在映射下相同，不是声称两块实际芯片未经复位便会随机上电到同一个值。

## 适用范围与拒绝条件

- 单个直接接到标量主输入的正沿时钟；支持同步复位/使能和 `$adff` 的直接主输入异步复位。没有使用 `async2sync`。
- 支持映射为触发器的小型 RAM，并核验初始化文件与初态位；不涉及工艺 SRAM 宏。
- 状态、输入是定义的 Boolean 值；内部未定义值以 Yosys 的 undef 模型和严格 `$eqx` 比较处理，不能推导完整 SV 四态事件语义。
- 多时钟、派生/门控时钟、锁存器、不识别单元、不完整/歧义映射、不同初始化或不同异步复位事件都拒绝。没有跳过未知单元或使用环境 assume。
- 重定时、重编码、多个 golden 状态位被合并到一个 gate 位等情况超出双射契约。即使实际复位后可能等价，也要先提出显式复位起点/更一般状态关系并完成新的基例证明。
- 共用 Yosys 前端/归一化是可信边界。本方法不证明前端本身符合全部 SV 标准，也不是独立形式证明证书。

## 使用与证据

```json
"formal_equivalence": {"required": true, "method": "state-bijection"}
```

```powershell
python -m fpga_agent lec examples/lec/counter
python -m fpga_agent lec examples/agent_validation/sync_fifo
python scripts/run_lec_matrix.py
python scripts/run_lec_matrix.py --cpu single --cpu pipeline
# 评估现有三种 CPU 核；任一未证明就返回非零，不改原 CPU 清单
python scripts/run_lec_matrix.py --cpu-cores --output reports/cpu-lec-matrix.json
```

新增产物：`gold.json`、`gate.json`、`state_relation.json`、`state_miter.json`、`gate_prepare.ys`。
`sat -verify` 失败时保存 `counterexample.json`。报告的 `state_relation` 给出完整映射、初值、时钟和复位信息；历史字段 `proven_cells` 在此模式表示检查的状态位数加输出位数。

LEC schema 已升为 2。证据额外绑定状态检查器源码；旧 schema 或检查器变更会使旧报告失效。
核验证据时会从两个网表重新构造完整状态关系和 miter，防止删掉状态位/比较项后仍把报告当作通过；交付包保留所有绑定文件。

本轮正例：8 位计数器、47 位状态的同步 FIFO、带初始化的两字节 RAM，以及现有单周期/两级流水 RV32I 核（分别 1,128/1,193 位状态，各 205 位输出）。反例覆盖错误 reset/enable/next-state/output、初值不一致、异步复位变化、部分映射和删减证明项。
SAT 前将纯组合 miter 进一步 techmap/opt 化简，两个 CPU 的时钟状态义务得以在本轮 60 秒预算内证明；没有减少义务或改变输入约束。
第二轮历史结果：多周期核因 `mem_offset[0]` 等位与 `effaddr` 的状态合并而被双射方法拒绝：golden 有 1,338 状态位，gate 有 1,336 位；这不是已确认的 CPU 功能错误。该强契约拒绝结论保留，第三轮另以显式复位后状态关系证明通过，不能混称为任意初态等价。
本机逐项结果与 CPU 未证明原因保存于根工作区 `validation/2026-11-17-state-lec-matrix.json`，该矩阵的 fail 不会被基础夹具 pass 覆盖。

最终软件回归为 217 passed / 1 skipped（Windows symlink 权限）；摘要及最终 JUnit 位于根工作区 `validation/2026-11-17-state-summary.json` 和 `2026-11-17-state-verified-pytest.xml`。一次较早回归的现有 gateway 回环 WinError 10053 报告亦保留；单独 10 次复测及最终完整回归通过，未改动该 gateway。
