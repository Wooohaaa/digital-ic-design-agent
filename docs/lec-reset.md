# 复位后等价：完整状态关系与合并状态

日期：2026-11-17，第三轮。实现位于 `fpga_agent/lec_state.py`、`lec.py`。
`reset-state-relation` 是独立的显式契约：从指定异步复位已断言并生效后的状态开始检查，不包含复位前行为。原 `state-bijection` 的任意初态集合检查继续保留，不会失败后自动降级。

## 基例与归纳步骤

1. 从公开状态信号名/索引/别名提出跨设计的等值类。允许一个类含多个 golden 或 gate 位，但两个设计的全部状态位都必须覆盖，每个类必须同时含两边成员。
2. 检查每个状态位都具有清单指定的直接主输入异步复位；信号、极性和类内复位值必须一致且确定。因此一次真实断言立即把全部成员带入这些等值类，建立基例。同步复位、未复位状态、未知复位值或缺少成员都会拒绝。
3. 将每个类替换为一个自由 Boolean 状态输入。对所有这样的状态和所有定义输入，用 SAT 检查**每一个 golden 和 gate 成员的下一状态**仍等于类的参照下一状态；不是只比较一对成员。
4. 检查全部外部输出相同。异步复位再次断言时重新建立同一关系，时钟沿的复位优先级也进入下一状态检查。

基例成立、每次事件保持关系、输出在关系下相同，故可推出复位后的任意长度对应轨迹相同。所有关系内状态均被检查，包括不可达状态。
原始初值可在两边不同，因为它们属于明确排除的复位前区域；这不允许省略 reset 基例，也不构成上电起就等价的证明。
这是项目自定义的对称等值关系证明，不应与 EQY 的 safe-replacement/X-propagation 语义混为一谈；后者的语义说明见 [YosysHQ 文档](https://yosyshq.readthedocs.io/projects/eqy/en/latest/xprop.html)。

## 配置与范围

```json
"formal_equivalence": {
  "required": true,
  "method": "reset-state-relation",
  "target_top": "rv32_core",
  "reset_start": {"signal": "rst_n", "active": 0}
}
```

`reset_start` 必须明确给出，当前仅接受 `signal` 和整数 `active`（0 或 1）。不自动设零、不假定某些程序执行过、不把同步 reset 伪装成异步 reset。
仍仅支持单个直接主输入正沿时钟、已识别组合单元和可降为触发器的状态；重编码、任意关系、门控时钟、多时钟、锁存器与 SRAM 宏尚不支持。

`target_top` 可选择清单中 RTL 的一个模块；报告同时给出 `design_top`、`proof_top`。例如 CPU 工程的设计顶层仍为 `rv32_soc`，证明目标为 `rv32_core`，不能据此宣称整个 SoC 已证明。全部 RTL 及目标配置都参与证据指纹。

报告使用 schema 3，并单独绑定 `contract`：包含“after-declared-asynchronous-reset”和“pre_reset_behavior: excluded”。删改契约、类成员、miter、配置或源码会使证据失效。核验时重新构造完整关系和 miter；交付包携带绑定产物。

## 当前验证与工程门禁

- 新增合并计数器：10 个 RTL 状态位对应 8 个网表状态位/8 个类，全部 18 个成员迁移受检查；仿真在时钟沿之间断言 reset，并检查 2048 周期的 reset/enable/hold/rollover。
- 多周期 RV32I 核：1338 个 RTL 位、1336 个网表位，1336 个类；205 位输出，所有 2674 个成员迁移均受检查。通过仅限显式复位后的核心行为。
- 单周期/两级流水核保留更强的 `state-bijection`，分别为 1128/1193 位状态。没有为让全矩阵变绿而降低其契约。
- 三个真实 CPU 工程清单均声明 required core LEC。TD 的公共入口先复用当前有效证明或重新证明；缺工具、未证明或失效会阻止厂商工具启动，TD 期间证据变化也使实现失败。`flow`、CPU full 流程和直接 `td` 共用该门禁。
- 自动测试覆盖：错误同侧合并成员迁移、reset 值/极性/信号错误、部分状态未异步复位、配置缺失、契约/类删改、不同 pre-reset 初值、目标范围变化、缓存失效和交付还原。

```powershell
python -m fpga_agent lec benchmarks/rv32_multi
python -m fpga_agent verify examples/lec/merged_counter
# 使用三个真实工程的已声明契约，保存真实工程所需证据
python scripts/run_lec_matrix.py --cpu-cores --project-cpus
# 单独评估“复位后”的临时 CPU 核快照
python scripts/run_lec_matrix.py --cpu-cores --reset-cpu multi
# 强契约负例检查：多周期核仍拒绝任意初态双射，返回非零是预期结果
python scripts/run_lec_matrix.py --cpu multi
```

仍信任共用 Yosys 前端和归一化；没有独立证明 SV 语义、ISA 正确性或工艺签核。本轮没有运行 TD、烧板或 ASIC signoff，新增远端 CI 配置也尚未执行。

本机最终完整回归：237 passed / 1 skipped，128.57 秒（skip 为 Windows symlink 权限）。七项真实工程矩阵全部通过，强初态多周期检查保持预期非通过；详细摘要与指纹位于根工作区 `validation/2026-11-17-reset-summary.json`。
