# tiny_soc interface and behavior

Module: `tiny_soc #(N=4, DW=32, BAUD_DIV=8)`.

Ports: `clk`, synchronous active-low `rst_n`, a host bus
`wb_we`/`wb_re`/`wb_addr[7:0]`/`wb_wdata[DW-1:0]`/`wb_rdata[DW-1:0]`, and a
serial output `uart_tx`.

## Memory map (byte addresses, one 32-bit word per address)

| address | register |
|---|---|
| 0x00 | CTRL: `[0]` start GEMM |
| 0x04 | STATUS: `[0]` GEMM done |
| 0x08 | RESULT: `C[0][0]` of the last GEMM |
| 0x0C | TXDATA: write low byte to transmit over UART |
| 0x10 | TXSTATUS: `[0]` UART busy |
| 0x40 + 4*i | A word `i` (row-major) |
| 0x80 + 4*i | B word `i` (row-major) |
| 0xC0 + 4*i | C word `i` (row-major, read-back) |

## Behavior

On reset, clear `done`, the UART state machine (`uart_tx` idle high), the GEMM
counters/accumulator, and the C memory. While idle, the host may write A/B
operands and read them back. Writing CTRL bit 0 pulses a start; the GEMM loop
runs `N^3` cycles accumulating `acc += A[i][k]*B[k][j]` with 32-bit wraparound
and stores `C[i][j] = ReLU(acc)` (sign-bit-set sums clamp to 0). When done,
`done` asserts and stays until the next start or reset.

Writing TXDATA starts an 8N1 UART frame: one start bit (0), eight data bits
LSB-first, one stop bit (1), each bit lasting `BAUD_DIV` clock cycles. `uart_tx`
is high while idle.

Arithmetic is `DW`-bit and wraps modulo 2^DW. No latches, no gated clocks.
