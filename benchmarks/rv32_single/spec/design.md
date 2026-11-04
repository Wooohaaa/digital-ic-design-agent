# RV32I single-cycle core

## Architecture

`rv32_core` fetches one 32-bit instruction per cycle from the combinational instruction-memory input and commits architectural state at the active clock edge. The program counter and 32 integer registers are the architectural state. Register `x0` is hardwired to zero. Instruction decode, ALU execution, branch resolution, effective-address calculation, and load extraction are combinational.

The supported ISA is the base RV32I integer instruction set used by this benchmark: LUI, AUIPC, JAL, JALR, all six conditional branches, integer register-register and immediate ALU operations, LB/LH/LW/LBU/LHU, SB/SH/SW, and FENCE as a no-op. M, C, and Zicsr instructions are not implemented. ECALL and illegal instructions halt with `trap=1` and do not retire. EBREAK retires with no register write, then halts with `trap=0`.

## Memory interface

Instruction memory is a combinational aligned-word interface; `imem_addr` is the current PC. Data memory uses an actual byte address on `dmem_addr` and an aligned 32-bit read value on `dmem_rdata`. Loads select and extend the appropriate byte or halfword using the low address bits. Stores shift their byte enables and store value into the addressed lanes. `dmem_valid` is asserted for legal, aligned loads and stores while the core is active. Stores commit on the rising edge where `dmem_valid` is high and `dmem_wstrb` is nonzero.

Misaligned halfword/word accesses, misaligned taken branch or jump targets, ECALL, and illegal encodings halt synchronously with `trap=1`; such instructions do not retire, and stores are suppressed. JALR clears target bit zero before checking four-byte instruction alignment. Reset is asynchronous active-low, initializes PC and all registers to zero, and gates off data-memory requests.

## Retirement interface

On a committed instruction, `retire_valid` is asserted for the cycle after its active edge, with the committed PC and instruction. `retire_rd` and `retire_wdata` identify a nonzero destination write; they are zero for instructions without a register write and writes to `x0`. Trap-causing instructions do not produce a retirement event.

## Implementation note

The implementation is intended for synthesis and uses inferred flip-flops for PC/register state plus combinational decode and datapaths. This artifact has not been run through the independent differential harness; verification status must be established by the benchmark owner.
