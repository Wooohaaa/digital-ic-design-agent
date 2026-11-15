# 版本化发布与聚合工程签核（阶段 7）

`icforge release PROJECT [--version VERSION]` 把前面所有阶段的工程证据**聚合**成一份版本化发布清单 `reports/release.json`，并调用交付打包生成带版本后缀的 zip。它是七阶段路线的最后一环：把结构/功能/CDC/时序/交付门一起纳入一个可追溯的发布记录。

它回答一个有哈希证据的问题：**在当前输入上，聚合工程评审的结论是什么、每个门的状态是什么、发布版本是什么？** 它**不**授予芯片签核。

## 为什么需要单独一条路径

前面的命令各自产出一份报告（`coverage.json`、`cdc.json`、`timing.json`、`implement.json`、`engineering-review.json`）。发布需要一份**单一、版本化、含全部门禁状态**的清单，且要绑定输入哈希。阶段 7 复用 `review_project` 的聚合结论，叠加版本号，然后交给既有的 `do_package` 生成交付物。

## 聚合语义（诚实边界）

- `run_release` 调用 `review_project(project, "fpga")` 拿到全部门禁的当前状态；**不会**把 `blocked`/`not_run` 改成 `pass`。
- `aggregate.artifact_ready` 来自聚合评审：它只要求**必填（`required`）门**通过。可选门（`CDC-001`/`COV-001`/`COV-002`/`TIM-001`/`SYNTH-001`/`DV-004` 等）未通过**不**阻断 `artifact_ready`，但会在 `gates` 里如实记录。
- `aggregate.engineering_signoff` 默认 `not_granted`。除非工程明确授予，发布**不等于**芯片签核。
- `aggregate.required_open_items` 是当前仍未通过的必填门数量，用于提示发布前仍需补的必填项。

## 版本

- `--version` 指定版本号；缺省用 `release.py` 的 `DEFAULT_VERSION`。
- 版本会写入交付 zip 的文件名与 zip 内 `delivery.json` 的 `release_version` 字段，便于追溯。
- 版本**不**改变门禁判定；它只给产物一个可追溯标签。

## 报告与状态

`reports/release.json` 记录：`kind=release`、`release_version`、`project`、`version`、`aggregate`（`artifact_ready`/`engineering_signoff`/`required_open_items`）、`gates`（`id -> {status, required}`）、`inputs`（RTL/约束/规格/测试台的哈希）、`reason`、`started_at`。

`inputs` 由 `input_hashes(project, manifest)` 计算，绑定 RTL、testbench、约束与 spec；改动任一输入后旧发布清单不再描述当前输入。

## 交付包

`release` 在写完 `release.json` 后调用 `do_package(project, simulation_only, version=...)`：

- 产物名：`<project>-delivery[-<version>].zip`（或 `simulation-only`）；
- zip 内 `delivery.json` 增加 `release_version` 字段；
- 交付门（`simulation_valid`/`implement_valid`/`artifact_ready`）沿用既有规则，版本号不放宽任何门。

## 审查门

发布本身**不新增门禁**；它消费 `review_project` 的全部门禁（含阶段 1–6 的 `SPEC-001`/`DV-001`…`DV-008`/`RTL-001`/`RTL-002`/`CDC-001`/`SYNTH-001`/`IMPL-001`…`IMPL-003`/`DELIV-001`/`COV-001`/`COV-002`/`TIM-001`/`BOARD-001`/`HW-001`）。`artifact_ready` 是聚合结论，不是新的通过条件。

## 夹具

- `examples/agent_validation/ice40_pwm/`：本机实测 `run_release(version="0.7.0")` 得到 `artifact_ready=true`、`engineering_signoff=not_granted`、`required_open_items=5`（`RTL-001`/`IMPL-001` 为 `blocked`，`RTL-002`/`IMPL-002`/`BOARD-001` 为 `review_required`），并生成 `deliverables/validation-ice40-pwm-delivery-0.7.0.zip`。

## 复现命令

```powershell
icforge release examples/agent_validation/ice40_pwm --version 0.7.0
icforge package examples/agent_validation/ice40_pwm        # 无版本后缀的旧行为
```

## 边界（未做，不声称）

- 发布清单是**聚合记录**，不是芯片级签核；`engineering_signoff` 仍为 `not_granted`。
- 不新增、不放宽任何门禁；版本号不改变通过条件。
- 不发布到远端、不签名、不做供应链/ provenance 验证。
- 未运行 post-route 仿真与实板验证时，`TIM-001`/`HW-001` 如实记 `blocked`/`not_run`。
