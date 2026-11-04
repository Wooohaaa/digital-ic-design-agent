# 工程审查与证据门禁

IC Forge 把规格、设计、验证、实现、集成的职责变成可追溯检查项。`review` 根据当前文件和工具报告重新计算结论，模型的回答不能把未执行项改成通过。这是工程流程控制；当前没有运行一组独立在线模型来代替人工签核。

## 使用

在 TUI 选择工程后：

```text
/lint
/verify
/review --simulation-only
/td
/review
/package
```

命令行也可在终端执行：

```powershell
icforge lint my-design
icforge verify my-design
icforge review my-design --scope simulation
icforge flow my-design
icforge review my-design --strict
```

`lint` 调用真正的 `Verilator --lint-only -Wall`，不关闭 warning fatal。没有工具时写 `blocked` 报告并返回 2。`review` 生成 `reports/engineering-review.json` 和 Markdown；`--strict` 在必要工程检查未关闭时返回非零。工程报告会提供具体的下一步动作，模型规划器也能读取这些结论。

## 职责与结论

| 职责 | 自动检查 | 仍需关闭的事项 |
|---|---|---|
| 需求 | 原始 brief、设计说明、逐项验证计划 | 需求完整性、假设与验收条件的审阅 |
| RTL 设计 | 当前源码的严格 Verilator lint 证据 | 结构设计、时钟复位、CDC/RDC、例外约束审阅 |
| 功能验证 | 仿真退出状态、断言错误、需求检查、CPU 独立参考 | 普通生成 testbench 的独立性、覆盖率闭合 |
| FPGA 实现 | 仿真有效性、源文件快照、setup/hold WNS 与 TNS、位流哈希 | 时序约束完整性、板卡 revision 与官方引脚资料确认 |
| 交付 | 包内实际字节校验、输入和报告未变化、原子替换交付包 | 实板观测与工程签核 |

报告明确区分 `pass`、`blocked`、`review_required`、`not_run`。`artifact_ready` 只说明选定范围的材料具备当前机器证据，不代表工程签核。交付包中的 `engineering_signoff` 当前保持 `not_granted`，并携带未关闭事项；没有伪造“团队已批准”状态。

## 需求与验证器

新生成的普通 RTL 工程会产生：

```json
{
  "schema_version": 1,
  "origin": "model-generated-unreviewed",
  "requirements": [
    {"id": "REQ-001", "description": "复位时输出必须清零", "method": "simulation-check"}
  ]
}
```

该文件位于 `spec/verification-plan.json`。testbench 必须先执行实际检查，再输出 `CHECK REQ-001 PASS`，最后才输出总的 `PASS`。每个目标必须恰好出现一次；缺失、重复、未知 ID、断言 ERROR/FATAL 或非零退出均失败。编译器输出中的 PASS 不算运行证据。

需求标记只证明对应检查被报告执行，不能自动证明检查语义充分。因此，模型生成的计划标记为未经独立审阅。普通 RTL 初次接受候选后，自动修复会冻结 testbench、checks、顶层标识和板卡映射，只允许修复 RTL；怀疑验证器错误时须明确修订验证计划，不能在修复循环中偷偷改弱判据。CPU 则持续使用受保护的主机 testbench 和独立指令参考。

## 证据失效与依赖

仿真 v2 证据同时绑定 RTL/testbench、顶层、验证配置、规格、验证计划及递归 literal include、literal `$readmemh/$readmemb` 数据文件。include 相对于当前文件或工程根目录解析，拒绝工程外依赖。无法静态确定的 include 或普通模块动态内存映像明确报错；CPU 可信 harness 的动态程序由固定参考生成器及 seed 配置绑定。

修改上述输入必须重新 `/verify`；旧版仿真报告也需重跑。验证器或规格改变不自动证明原来的通过仍有效。CPU 证据额外核对完整用例名称、数量、每项结果、定向功能覆盖点和板级 SoC 结果。

TD 快照包含 RTL 的头文件与数据依赖，编译文件顺序也纳入实现指纹。完整实现必须具备有限、非负 setup/hold WNS，以及均为零的 setup/hold TNS；摘要必须匹配实际 run 目录的最终时序报告，超出器件资源容量也拒绝交付。报告单独呈现工具给出的 STA 覆盖率；时序数字闭合不意味着约束完整性或 CDC 自动闭合，未约束路径和例外仍需审阅。

## 交付一致性

交付先写独立临时 ZIP，完成后重新检查源文件、报告、实现证据和 ZIP 内的 RTL/验证文件/位流字节。若内容变化，拒绝发布该临时包，并保留上一份交付。`MANIFEST.sha256` 给出包内每个文件的 SHA-256，ZIP 旁边的 `.zip.sha256` 校验整个交付包。

普通仿真材料可通过 `--simulation-only` 交付；完整 FPGA 包必须通过当前仿真与实现证据门禁，并包含工程审查报告。代码覆盖、formal/LEC、post-route 仿真、实板验证尚未接入，不会作为已完成工作报告。

## 方法来源

- [OpenTitan 验证方法](https://opentitan.org/book/doc/contributing/dv/methodology/index.html)：参考需求、testplan、覆盖计划与回归结果的追踪关系。
- [OpenTitan 硬件开发阶段](https://opentitan.org/book/doc/project_governance/development_stages.html)：参考设计进度、验证进度和签核分别管理；本项目不声称达到其 V2/V3 等级。
- [Ibex 验证阶段](https://ibex-core.readthedocs.io/en/stable/03_reference/verification_stages.html)：参考按具体配置记录验证状态，而非从一个成功案例推断所有配置成熟。
- [Icarus 编译选项](https://steveicarus.github.io/iverilog/usage/command_line_flags.html)：`-grelative-include` 与 `-I` 的依赖搜索规则。
- [Verilator 参数](https://verilator.org/guide/latest/exe_verilator.html)：严格 lint 路径及 warning fatal 行为。
- [RISC-V 架构测试](https://github.com/riscv/riscv-arch-test)：官方明确指出架构认证测试不能替代完整处理器验证；当前尚未接入 ACT4。
- [SymbiYosys 配置参考](https://github.com/YosysHQ/sby/blob/main/docs/source/reference.rst)：后续形式验证必须分别记录 prove/bmc/cover、假设和深度，不能把有界通过写成无界证明。
