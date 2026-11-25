# Stage 2 RV32I acceptance

The three RV32I cores share the execution environment below. The checked-in model responses, compiler inputs and verification programs can be replayed from the repository root.

## Frozen execution environment

- RV32I 2.1, fixed 32-bit instruction length, a single hart, little-endian words and an active-low reset at PC zero. Register x0 is hardwired.
- The core receives aligned words on combinational memory reads. A write takes effect on a rising edge when the valid signal and byte mask authorize it. The directed and random test corpus uses RAM in the fixture's address map. There is no external access-fault input.
- Misaligned halfword or word accesses and taken misaligned jumps or branches stop with `trap`. Reserved encodings and unsupported M, C, Zicsr and Zifencei instructions also stop with `trap`; ECALL follows the same terminal convention. The faulting instruction does not retire and may not write memory. A prior store remains visible. A younger store has no effect and a stopped core remains quiescent.
- For this harness, EBREAK is a terminal marker that retires with no architectural write. FENCE is checked in this fixture's ordered, single-hart, non-MMIO environment. Multihart ordering, asynchronous interrupts, privilege, trap causes as architectural outputs, and a trap-handler resume protocol belong to a separate platform contract.

The base ISA defines the alignment and environment boundaries. [RISC-V RV32I 2.1](https://docs.riscv.org/reference/isa/v20260120/unpriv/rv32.html)

## Run the acceptance gates

The runner requires Icarus, Yosys or YoWASP Yosys, Clang with RISC-V support, LLD, the sibling LLVM inspection tools, and the pinned ACT checkout described in [ACT signatures](rv32-act.md). Install optional Unicorn 2.1.4 for the independent engine. Run with a new output directory.

```powershell
uv run --locked --with unicorn==2.1.4 --with yowasp-yosys==0.71.0.0.post1285 python -m fpga_agent.cpu_stage2 --act-suite ../act-2.7.4 --output reports/stage2
```

The command replays the saved model responses through the CPU design entry point, runs the original 114-case benchmark and board-wrapper signature, and checks 39 retiring operations, 12 branch outcomes, and 20 aligned byte-lane combinations. It synthesizes each result and validates its required core LEC. Single-cycle and two-stage pipeline cores use state bijection. The multicycle core uses the proven relation after reset.

The independent suite compares execution, retirement and memory effects. Native Unicorn exception callbacks are retained separately from the local EEI's alignment and extension decisions. The fault matrix checks both RTL and netlists, including an earlier committed store, rejection of a younger store, and four observed stopped cycles. ACT runs the same 38 upstream programs on RTL and netlists and checks that both receive identical images and reference signatures. The negative controls retain the earlier multi-cycle ALU failure and detect an introduced wire-level leak that writes memory from a trapping store.

The report includes simulated cycles, retirements, stores and a generic Yosys cell inventory. These metrics use the frozen benchmark and do not express FPGA timing or silicon area. The tracked generation replay uses archived responses and does not perform new live model inference. ACT4 platform certification uses its own DUT configuration and Sail reference flow. [RISC-V ACT4 platform requirements](https://github.com/riscv/riscv-arch-test/tree/act4)
