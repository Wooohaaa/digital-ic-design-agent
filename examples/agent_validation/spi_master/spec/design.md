# SPI master interface and behavior

Module: `spi_master #(CLK_DIV=2, CPOL=0, CPHA=0)`.

Ports: `clk`, synchronous active-low `rst_n`, one-cycle `start`, `tx_data[7:0]`, `rx_data[7:0]`, `busy`, one-cycle `done`, `sclk`, `mosi`, `miso`, active-low `cs_n`.

Accept `start` only while idle. Assert `cs_n` for exactly one eight-bit transfer and hold it low until the final trailing edge completes. Idle `sclk` equals CPOL, idle CS is high, and busy is asserted during the transfer. Transfer MSB first. CPHA=0 samples MISO on leading edges and changes MOSI on trailing edges; CPHA=1 changes MOSI on leading edges and samples MISO on trailing edges. A leading edge moves SCLK away from CPOL; a trailing edge returns it to CPOL. Drive the first MOSI bit before the first sampling edge in both modes. Latch `rx_data` at completion and pulse `done` for one `clk` cycle.

`CLK_DIV` is the number of system-clock cycles per SCLK half-period and must be at least 2. Support all four CPOL/CPHA combinations. No pins are mapped in this synthesis-only fixture. Use a single system clock and synthesizable state; do not gate `clk`.
