# v0.4.0 工程流程验收

执行日期：2026-11-04。目标：HX1P35A / PH1P35MDG324；实现工具：本机 Tang Dynasty V6.2.168116。

本轮重新使用已有的心跳和三份 RV32I RTL，运行 Icarus 验证、TD 综合、布局布线、完整交付，并在新的临时目录解压交付包后重新检查全部仿真/实现证据。没有调用新的在线模型生成 CPU，也没有下载开发板。

| 工程 | 仿真 | Setup WNS ns | Hold WNS ns | Setup/Hold TNS ns | STA 覆盖率 | 交付解压后证据 |
|---|---|---:|---:|---|---:|---|
| 心跳 | 通过 | 16.332 | 0.154 | 0 / 0 | 88.51% | 通过 |
| RV32I 单周期 | 114 程序，64 随机种子，SoC 通过 | 0.463 | 0.073 | 0 / 0 | 92.36% | 通过 |
| RV32I 多周期 | 114 程序，64 随机种子，SoC 通过 | 7.284 | 0.034 | 0 / 0 | 92.24% | 通过 |
| RV32I 流水线 | 114 程序，64 随机种子，SoC 通过 | 0.696 | 0.039 | 0 / 0 | 92.85% | 通过 |

最终自动化回归：**79 项通过**。检查包括伪 PASS、零退出码的断言 ERROR、缺失/重复需求检查、验证器被自动修复改弱、include/ROM/顶层/源文件顺序变化、CPU 用例或覆盖点缺失、TNS 异常、源快照和位流变化、打包过程中源文件被短暂替换，以及 TUI 输入排队和审查结果展示。

安装包额外检查在临时目录安装 wheel，核验模块来自该 wheel、版本为 0.4.0、TUI 样式与新增工程审查模块随包提供。逐工程校验值见 [机器记录](engineering-validation.json)，CPU 覆盖与资源统计见 [suite-report.json](../benchmarks/suite-report.json)。

## 未关闭事项

- 本机未安装 Verilator：严格 lint 状态为 `blocked`，没有把 Icarus 编译当作 lint 完成。
- STA 覆盖率低于 100%：须审查未约束端点、IO delay、时钟复位和例外条件。非负 WNS 与零 TNS 仅说明已分析路径达到当前约束。
- 旧 CPU 示例缺少单独的原始 brief；新 `cpu-design` 会自动保存执行契约和架构需求。没有追写一份文档冒充原始审阅。
- 普通生成 testbench 仍需独立审阅或参考模型；需求标记不能证明检查语义充分。
- 代码覆盖闭合、正式 CDC/RDC、formal/LEC、post-route 仿真和实板观察尚未完成。

因此四份交付均标记 `artifact_ready=true`、`engineering_signoff=not_granted`，随包保留各职责的待办项。方法与参考资料见 [工程审查流程](engineering-workflow.md)。
