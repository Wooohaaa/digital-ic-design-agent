---
name: hx1p35a-delivery
description: HX1P35A 从 RTL 验证、安路 TD 实现到可追溯交付包的工程闭环。
---
# HX1P35A implementation and delivery

1. Read the board profile, port map, clock/reset constraints and accepted specification. Pin assignments must come from the board documentation and validated profile.
2. Verify the exact current RTL before synthesis/route. Repair the first counterexample while keeping the trusted verification contract intact.
3. Use td for full FPGA implementation. Inspect reports/td.json for bitstream hashes, setup/hold WNS, TNS, resource use and coverage. Missing reports or negative slack are failures.
4. Use review to expose open engineering checks. Distinguish artifact readiness from engineering signoff, board acceptance and ASIC physical signoff.
5. Package only after current simulation and implementation evidence pass. The host checks inputs, archive contents and bitstream fingerprints.
6. If programming or board measurement is requested outside available tools, explain the missing capability. A generated bitstream or successful model response is not physical-board evidence.
