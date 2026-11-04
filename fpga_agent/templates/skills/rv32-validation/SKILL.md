---
name: rv32-validation
description: RV32I CPU 独立验证、失败反例定位、提交级比较及修复复验流程。
---
# RV32I independent verification

1. Inspect the current manifest, specification and first failing case. Use search/inspect to read beyond source excerpts; inspect reports/simulation.json and the actual error log.
2. Preserve the trusted reference, testbench and accepted checks during automatic RTL repair. Diagnose the first PC/instruction/writeback/store mismatch, not just the final FAIL.
3. Use cpu-design for a candidate core and verify for current existing RTL. Re-run independent retirement comparison after changes. Supported baseline is RV32I without M/C, CSR, interrupts or privileged execution.
4. Check branches, wrong-path stores, x0, signed shifts, byte enables, alignment traps and reset boundaries against the execution contract.
5. Historical passing runs are hints. Only current source/configuration fingerprints and the host's verification gates establish current success.
6. Report unsupported ISA features, missing tools and open checks explicitly; do not claim architecture certification or formal proof from the local regression.
