# SPI master validation task

Implement an 8-bit, MSB-first SPI master with programmable clock divider and CPOL/CPHA parameters. Preserve the module and port list in `rtl/spi_master.v`. The fixed host testbench instantiates all four SPI modes at `CLK_DIV=2` and checks three transactions per mode.

The agent owns only `rtl/spi_master.v`; the host owns and SHA-256-pins `tb/tb_spi_master.v`.
