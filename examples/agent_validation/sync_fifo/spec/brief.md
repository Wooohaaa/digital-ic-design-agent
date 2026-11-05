# Synchronous FIFO validation task

Implement a parameterized, single-clock FIFO for HX1P35A-class FPGA logic. The acceptance instance is 8 bits wide and four entries deep. Use synchronous active-low reset. Keep the public module and ports in `rtl/sync_fifo.v` unchanged.

The agent owns only `rtl/sync_fifo.v`. The host owns the fixed scoreboard in `tb/tb_sync_fifo.v` and its SHA-256 pin in `project.json`.
