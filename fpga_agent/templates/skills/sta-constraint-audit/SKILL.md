---
name: sta-constraint-audit
description: 审查 FPGA 实现中的时钟、IO 时序、STA 覆盖率和未约束端点，识别不能由 WNS 单独说明的风险。
---
# STA and constraint audit

1. Read the current `reports/td.json`, the exact SDC/ADC files in `project.json`, and the board profile. Verify report inputs match the current source and constraint fingerprints.
2. Enumerate primary/generated clocks and their periods. Check clock uncertainty, input/output delays, clock groups and exceptions against the actual interfaces and board timing assumptions.
3. Inspect the vendor report for unconstrained paths/endpoints and the denominator behind reported STA coverage. Record each excluded endpoint and its reason; never infer 100% coverage from positive WNS.
4. Separate setup, hold, recovery/removal, pulse-width, and unconstrained-path findings. A positive setup WNS alone does not close the timing review.
5. When a report omits endpoint detail or a required timing model, mark the audit blocked/review-required and name the missing report or board data. Do not fabricate constraints or hide paths with broad false-path exceptions.
6. Run the vendor flow again after a constraint edit and inspect the exact new report. FPGA timing evidence is not ASIC signoff.
