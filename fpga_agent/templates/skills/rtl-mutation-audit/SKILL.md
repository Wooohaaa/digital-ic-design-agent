---
name: rtl-mutation-audit
description: 用隔离的 RTL 变异仿真检查 testbench 是否能发现常见逻辑错误，并把幸存变异映射回规格。
---
# RTL mutation audit

1. Start only after the ordinary generic Icarus baseline passes. Run `icforge mutation-audit PROJECT`; the CLI uses a deterministic seed-ranked sample of single-operator mutations in a disposable copy and never edits project RTL or the testbench.
2. Read `reports/mutation.json`. Confirm `status=complete`, the baseline passed, and `simulation_inputs` still match the project. A partial, blocked, or stale audit is not evidence of testbench strength.
3. Interpret `killed`, `survived`, `compile_error`, `timeout`, and `inconclusive` separately. Compile failures are not killed mutants; survivors may be equivalent. The observed kill rate is diagnostic, not a pass threshold, proof, or signoff.
4. For survivors, inspect the affected RTL line and map the behavior to an explicit requirement. Propose a new stimulus/check only when the requirement supports it. Do not weaken or rewrite a trusted testbench just to raise the score.
5. Re-run the baseline, mutation audit, and engineering review after approved verification changes. Reports are fingerprinted to the current RTL, testbench, specification, and verification configuration.
6. This generic audit does not replace the specialized RV32I, HDMI, TinyStories, formal, CDC/RDC, or board-level acceptance flows.
