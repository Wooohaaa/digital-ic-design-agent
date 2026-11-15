# Class-based environment validation task

Implement a registered 8-bit ALU under `rtl/alu8.sv` and verify it two ways in the same project: a conventional Icarus self-checking testbench (`tb/tb_alu8.sv`) and a minimal, self-authored class-based (UVM-style) environment under `verif/` driven by Verilator.

The class environment is **not** a full UVM library. It is a small transaction / driver / scoreboard set that pins the vendor-neutral flow's ability to elaborate and execute SystemVerilog classes, a `virtual interface` and `randomize()` on the same RTL that the Icarus path checks.

The agent owns only `rtl/alu8.sv`. The host owns the fixed testbenches and the SHA-256 pin in `project.json`.
