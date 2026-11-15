# SystemVerilog datapath validation task

Implement a parameterized, single-clock accumulator datapath in synthesizable SystemVerilog under `rtl/sv_datapath.sv`. The acceptance instance is 8 bits wide and four stages deep. Use synchronous active-low reset and keep the public module and ports unchanged.

This fixture pins the vendor-neutral flow's SystemVerilog support: `always_ff`/`always_comb`, `typedef enum`/`typedef struct packed`, `logic`, parameters, `$clog2` and `generate` loops. It is intended to pass Icarus simulation, strict Verilator lint and Yosys synthesis from the same source.

The agent owns only `rtl/sv_datapath.sv`. The host owns the fixed scoreboard in `tb/tb_sv_datapath.sv` and its SHA-256 pin in `project.json`.
