# RV32I pipeline core design

## Scope

`rtl/rv32_core.v` implements a compact, single-issue two-stage RV32I integer
core. Stage one presents `pc_fetch` to the combinational instruction memory and
captures the returned instruction and its PC in the fetch/execute register.
Stage two decodes, reads the register file, executes, performs any data-memory
access, updates architectural state, and emits the retirement record at the
next rising edge. The next sequential instruction is fetched while the current
instruction executes. Taken branches and jumps flush that fetched instruction
and redirect the fetch PC.

The register file has asynchronous reads and synchronous writes. Since an
instruction reads its sources in execute while the previous instruction writes
at the edge entering execute, adjacent RAW dependencies observe the newly
written value without an extra bypass path or stall.

## Memory interface

Instruction memory is a combinational 32-bit word interface at `imem_addr`.
Data loads consume the 32-bit word containing `dmem_addr`; byte and halfword
loads select the addressed little-endian lane. Stores expose the actual byte
effective address, lane mask, and lane-shifted write data. The external memory
performs writes on a rising edge when `dmem_valid` and `dmem_wstrb` are active.
The core suppresses store requests on reset, while halted, for bubbles, and for
illegal or misaligned accesses.

## ISA and termination

Supported operations are RV32I `LUI`, `AUIPC`, `JAL`, `JALR`, all six integer
branches, integer register and immediate ALU operations, `LB/LH/LW/LBU/LHU`,
`SB/SH/SW`, and `FENCE` as a no-op. `x0` always reads as zero and writes to it
are discarded. `ECALL`, unsupported encodings, misaligned data accesses, and
misaligned taken control targets set `halted` and `trap` without retiring the
faulting instruction. `EBREAK` retires with destination zero, then halts without
setting `trap`.

Retirement outputs are registered pulses: each legal instruction emits its PC,
encoding, destination, and committed write data at its commit edge. An
instruction that does not write a nonzero destination reports `rd=0` and
`wdata=0`.
