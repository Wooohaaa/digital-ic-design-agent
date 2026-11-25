# Stage 3 RV64 engineering baseline

The application processor target is RVA23S64 1.0. The current implementation provides a bounded RV64I execution environment, a machine-only trap and return configuration, and a standalone Sv39/Svade walker. This captures three independently testable pieces of the full processor. [RVA23S64 1.0](https://docs.riscv.org/reference/rva23/v1.0/rva23-profiles.html) · [Machine architecture 1.13](https://docs.riscv.org/reference/isa/priv/machine.html) · [Supervisor architecture 1.13](https://docs.riscv.org/reference/isa/priv/supervisor.html)

| Check | Implemented contract and acceptance |
| --- | --- |
| RV64I | 30 arithmetic/computational instructions, all six conditional branches, JAL/JALR, seven loads, four stores, FENCE, and fixture ECALL/EBREAK handling. Aligned 64-bit byte-lane bus and terminal faults. Unicorn compares PC, instruction, destination and value at retirement, every store, and final RAM. |
| Machine runtime | Direct `mtvec`, `mepc`, `mcause`, `mtval`, `mscratch`, and the MIE/MPIE bits of `mstatus`. The six CSR forms and MRET are exercised by an independently specified trace. MPP is hardwired to machine mode. Tests check ECALL, illegal encoding, misaligned jump and write-access fault, and return to the handler-selected address. |
| Architectural tests | Fixed ACT 2.7.4 RV64I test directory, compiled from original source by LLVM. The local model header aligns signatures to XLEN. Unicorn and both hardware implementations must match at every signature word and end at the expected EBREAK. The same software path also boots a freestanding LLVM-compiled C checksum through the LP64 calling convention. |
| Translation | Standalone 3-level Sv39 walker. Checks canonical addresses, 4 KiB and superpage mappings, permissions, SUM/MXR, Svade A/D checks, invalid PTE and PTE access faults. Tests compare each PTE request and result, exercise stalls and reject a SUM-permission mutation. |
| Synthesis and proof | Separate whole-core state-bijection proofs for the two parameter configurations, plus state-bijection for the walker. The LEC adapter elaborates manifest overrides before proof and binds them in the configuration fingerprint. The simulator replays the proof's actual generic netlists. |
| Physical baseline | Fixed seed 7, ECP5 LFE5U-85F CABGA381, 20 MHz target and machine-enabled core. Out-of-context nextpnr measures routed clock frequency and utilization. The report excludes I/O delay, a complete FPGA system, ASIC libraries, power and multi-corner timing. |

The core uses a single-cycle retirement path and combinational instruction and data reads. A data store commits only with its byte mask on the rising edge. The platform holds fetch and data responses stable for that cycle. Aligned out-of-range transactions may be presented as requests for fault determination, and never commit a write. The fixture treats unimplemented instructions as traps and retires EBREAK as a local stop marker. The machine test harness does not delegate or service interrupts. In the ACT harness only, text reads are allowed and program memory is immutable.

## Reproduce from the repository root

The first three commands use Icarus and Yosys. ACT also needs Verilator with a C++20 compiler and make for the netlist, RISC-V-enabled LLVM with Clang, LLD, `llvm-nm`, `llvm-objcopy`, and the original checkout at `96e3589a867955e94c95ba098822da54d3ae39c0`. ECP5 additionally requires native Yosys with ECP5 support and nextpnr-ecp5. The CI workflow pins the CAD suite and uploads reports. Run each command with a fresh output directory.

```text
uv run --locked --with unicorn==2.1.4 --with yowasp-yosys==0.71.0.0.post1285 python -m fpga_agent.cpu_rv64 --output reports/rv64-runtime
uv run --locked --with unicorn==2.1.4 python -m fpga_agent.rv64_act --suite ../riscv-arch-test --proof-project reports/rv64-runtime/rv64i/project --output reports/rv64-act
uv run --locked --with yowasp-yosys==0.71.0.0.post1285 python -m fpga_agent.sv39_accept --output reports/sv39-walker
uv run --locked python -m fpga_agent.rv64_fpga --output reports/rv64-ecp5
```

The earlier integer-only configuration and regression remain independently runnable. [M1 integer execution contract](rva23-m1-integer.md)

## Remaining Stage 3 exit conditions

| Gate | Dependency or remaining implementation |
| --- | --- |
| Full profile | Integrate effective privilege modes, PMP/PMA, the walker and translation cache, S-mode, delegation and interrupts. Add mandatory profile extensions, including M/A/F/D/C/B/V, supervisor and hypervisor features. Run profile-aware ACT4 and system verification. The historical I-suite is the current scalar baseline. |
| System and performance | Define memory and I/O platform, cache/coherence, interrupts and timers, then boot system software. Select workload, performance and power budgets and compare pipeline/cache options against the same benchmark. The current simulated workload and out-of-context FPGA route provide starting measurements. |
| ASIC signoff | Select an available process, licensed or open PDK, libraries, macros, packaging and test access. Create full-chip timing, CDC/RDC, DFT, power, DRC/LVS and package plans. Run the chosen signoff flow on the integrated design with explicit corners and constraints. |
| Tapeout and silicon | Establish foundry or shuttle access, delivery and funding. Complete signoff, tapeout acceptance and silicon bring-up. |
