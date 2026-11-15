# Tiny SoC validation task — RSI roadmap stage 7

Build a **minimum SoC-level integration** for RSI stage 7: memory-mapped
interconnect, a compute accelerator, and serial I/O. This is the
"SoC-ify the whole system" direction — a CPU (or host) talks to a compute
accelerator over a memory bus, and results are communicated over a serial
protocol.

The acceptance instance is `tiny_soc #(N=4, DW=32, BAUD_DIV=8)` in
`rtl/tiny_soc.sv`. Keep the module name and ports unchanged.

The SoC exposes:
- a host write/read bus (`wb_we`/`wb_re`/`wb_addr`/`wb_wdata`/`wb_rdata`)
- a memory-mapped register file with an address decoder
- an integer GEMM accelerator (`C = ReLU(A*B)`) with on-chip operand and
  result SRAM
- an 8N1 UART transmitter (`uart_tx`)

The agent owns only `rtl/tiny_soc.sv`. The host owns the fixed testbench in
`tb/tb_tiny_soc.sv` and its SHA-256 pin in `project.json`; the testbench holds
the matrices, the independent reference model, and a per-cycle UART capture.

## Scope boundary

This fixture exercises the stage-7 minimum: a memory-mapped bus, an address
decoder, a compute accelerator, a serial I/O path, and a reset path. It does
**not** implement a CPU core, AXI/NoC, MMU, DMA, multi-clock domains, power
management, or a physical layer. It is a single-clock, single-domain SoC tile,
not a commercial SoC and not a semiconductor-level architecture redesign.
