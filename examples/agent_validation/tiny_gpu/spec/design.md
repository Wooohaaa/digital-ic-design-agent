# tiny_gpu interface and behavior

Module: `tiny_gpu #(LANES=4, NREG=8, IMEM_DEPTH=64, DMEM_BANK_DEPTH=16, DW=32)`.

Ports: `clk`, synchronous active-low `rst_n`, `start`,
host instruction port `imem_wren`/`imem_addr`/`imem_wdata`,
host data port `dmem_wren`/`dmem_addr`/`dmem_wdata`/`dmem_rden`/`dmem_rdata`,
and `done`.

## Execution model

All `LANES` lanes execute the same instruction stream in lockstep under a single
program counter. Each lane owns a private `NREG x DW` register file and a private
`DMEM_BANK_DEPTH`-word data bank. The host data port addresses memory as an
absolute word index `lane * DMEM_BANK_DEPTH + offset`.

On reset (`rst_n` low) the program counter, `running`, `done` and every lane
register file are cleared. The core stays idle until `start` is pulsed, then
feches and executes from instruction address 0. After an `EXIT` instruction the
core stops and asserts `done` (which stays asserted until the next `start`).
`dmem_wren` is only honored while the core is idle, so host loads cannot race
execution.

## Instruction subset (PTX-like)

Encoding: `{op[3:0], rd[2:0], ra[2:0], rb[2:0], rc[2:0], imm[15:0]}`.

| op | PTX-like name | behavior |
|---|---|---|
| 0  | mov (imm) | `rd = zero_extend(imm)` |
| 1  | mov       | `rd = ra` |
| 2  | add       | `rd = ra + rb` |
| 3  | sub       | `rd = ra - rb` |
| 4  | mul       | `rd = ra * rb` |
| 5  | mad       | `rd = ra * rb + rd` |
| 6  | ld        | `rd = dmem[lane][imm[3:0]]` |
| 7  | st        | `dmem[lane][imm[3:0]] = ra` |
| 8  | setp.lt   | `rd = (ra < rb) ? 1 : 0` |
| 9  | bra       | `pc = imm[5:0]` |
| 10 | exit      | stop; assert `done` |

Arithmetic is `DW`-bit and wraps modulo 2^DW. `LD`/`ST` use the low
`log2(DMEM_BANK_DEPTH)` bits of `imm` as the word offset inside the lane's own
bank; `BRA` uses `imm[5:0]` as the target address. No latches, no gated clocks.
