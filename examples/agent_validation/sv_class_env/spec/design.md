# ALU interface and behavior

Module: `alu8`.

Ports: `clk`, synchronous active-low `rst_n`, `en`, `a[7:0]`, `b[7:0]`, `op[1:0]`, `y[7:0]`.

On a rising edge with reset asserted, `y` becomes zero. Otherwise, when `en` is high, `y` captures the selected operation: `op==0` add, `op==1` subtract, `op==2` bitwise AND, else bitwise OR. When `en` is low, `y` holds its value. All arithmetic wraps modulo 256.

Two independent verification paths must pass on the same RTL:

- `tb/tb_alu8.sv`: a conventional Icarus self-checking testbench (runs with `icforge verify`).
- `verif/tb_alu8_env.sv` plus the `alu8_*` classes: a class-based environment with a randomized transaction, a driver bound to a `virtual interface` and a self-checking scoreboard (runs with `icforge svsim`).

Keep the implementation synthesizable and vendor-neutral: no vendor primitives, no inferred latches and no board pins (this is a simulation/verification fixture).
