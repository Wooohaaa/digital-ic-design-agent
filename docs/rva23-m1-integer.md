# Stage 3 M1 RV64I integer execution

Stage 3 targets the RVA23S64 1.0 application processor profile. M1 freezes a single-hart, little-endian RV64I 2.1 integer execution contract. The full profile also requires architectural support for vector, compressed and atomic instructions, supervisor architecture 1.13, Sv39 and additional supervisor and hypervisor facilities. [RVA23S64 profile](https://docs.riscv.org/reference/rva23/v1.0/rva23-profiles.html)

## Execution contract

- Active-low synchronous reset clears the PC and all integer registers. The instruction input is the 32-bit word at the combinational fetch address. The only progression in this milestone is PC + 4. Both visible PCs are structurally four-byte aligned.
- Accepted instructions are LUI and AUIPC and the 28 computational instructions in OP, OP-IMM, OP-32 and OP-IMM-32. Full-width shifts use six shift bits. Word shifts use five, and all word results sign-extend to 64 bits. Reads of x0 return zero and writes are discarded. [RV64I 2.1](https://docs.riscv.org/reference/isa/v20240411/unpriv/rv64.html)
- EBREAK retires once as the harness stop marker. ECALL and unsupported encodings stop without retirement. The fixture applies that rule to reserved immediate word shifts and rejects unimplemented instructions, including otherwise standard instructions. While halted, the fetch PC holds and no further retirement is valid until reset.
- The milestone has a fixed-latency instruction input. Further milestones cover branches, jumps, loads, stores, privilege, virtual memory, profile extensions, system software and physical implementation. PDK, performance thresholds and ASIC signoff remain open.

## Acceptance

Run from the repository root with Icarus Verilog on PATH. The pinned Unicorn binding runs in RV64 mode and the existing Yosys LEC adapter produces the generic netlist.

```powershell
uv run --locked --with unicorn==2.1.4 --with yowasp-yosys==0.71.0.0.post1285 python -m fpga_agent.cpu_stage3 --output reports/stage3-m1
```

The runner creates a fresh directory, snapshots the project, and uses Unicorn to execute each ordinary instruction. RTL and the proved netlist receive the same programs. The checker compares the retired PC, instruction, destination and 64-bit value and also checks reset, fetch and halt signals each cycle. The fixture defines the expected terminal behavior separately from the Unicorn computation. It includes 8 directed programs, 32 fixed random programs by default and 11 trap programs. Directed programs cover the 30 computational encodings, shift and word-sign boundaries, and every register address. A separate program reads each register after reset.

Acceptance requires complete state-bijection LEC of the core against the generic netlist, identical RTL and netlist traces, and observed counterexamples from both a faulty word sign extension and a decoder that accepts a reserved word shift. The output contains the reference trace, implementation traces, LEC report and SHA-256 fingerprints. The broader runtime and profile gates are covered in the [Stage 3 baseline](rva23-stage3.md).
