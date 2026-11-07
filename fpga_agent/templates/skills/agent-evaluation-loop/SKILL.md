---
name: agent-evaluation-loop
description: 用固定工程任务和宿主验收数据比较 agent 模型、工作模式与调度策略，避免凭单次演示判断改进。
---
# Agent evaluation loop

1. Capture `icforge task metrics --workspace WORKSPACE` before changing prompts, routing, context policy, or delegation limits. Metrics omit task text and credentials.
2. Reuse a fixed, version-controlled workload with a named acceptance target. Include a simple FIFO, SPI protocol controller, APB peripheral, and one CPU case when comparing digital-design behavior.
3. Change one harness variable at a time: model/backend, engineer versus orchestration mode, or parallelism. Keep design inputs, tool versions, worker file scopes, and acceptance checks fixed.
4. Compare host-gated completion rate, mean decision steps, elapsed time, failed tool actions, and failed delegated jobs. Record sample counts; a one-run difference is weak evidence. Token and cost usage are not collected by the current metrics command.
5. Inspect task step logs for representative failures before changing behavior. Turn a recurring failure into a deterministic fixture or a skill instruction, then rerun the same workload.
6. Treat task metrics as process evidence only. A completed task means its configured host gate passed; it does not imply engineering signoff or physical-board validation beyond that gate.
