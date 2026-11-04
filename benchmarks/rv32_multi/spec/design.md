# RV32I multi-cycle core

The core uses separate FETCH, DECODE, and COMMIT states. FETCH captures the instruction and its PC, DECODE validates the encoding and computes the result or effective address, and COMMIT performs register or memory effects and emits one retirement record. Each instruction takes at least three clock edges from fetch to retirement, making the implementation structurally multi-cycle.

The memory interface supplies combinational aligned 32-bit words. `dmem_addr` is the byte effective address; stores shift byte lanes and assert byte strobes only during a valid store commit. Loads select and extend bytes or halfwords from the aligned returned word. Reset is asynchronous active-low and initializes the PC and register file to zero.

Supported operations are RV32I integer control flow, integer ALU, loads/stores and base FENCE as a no-op. M, C, CSR and other unsupported encodings trap. ECALL and misaligned accesses or control-flow targets trap without retirement or side effects. EBREAK retires with no destination write and then halts without trapping.
