# PWM breathing generator

Module: `pwm_breath`.

Ports: `clk`, synchronous active-low `rst_n`, `duty[7:0]`, `pwm` (output), `level[3:0]` (output).

Behavior:

- A free-running 8-bit `counter` increments on every rising edge; reset clears it to 0.
- `pwm` is registered: on the rising edge it captures `counter < duty`, where `counter` is the value before the edge. Over any 256 consecutive edges the counter visits every value once, so the number of high edges equals `duty`.
- `level` is registered and tracks `counter[7:4]`, i.e. the top nibble of the counter.

The design is plain Verilog-2001, synthesizable and vendor-neutral. It carries no vendor primitives and no board-specific logic; the board pin map lives only in `constraints/pins.pcf`, which the host fixes.

Implementation target: `lattice-ice40-hx1k` (iCEstick, iCE40HX1K-TQ144) through the open Yosys `synth_ice40` -> `nextpnr-ice40` -> `icepack` flow. A passing run produces a real `.bin` bitstream and a nextpnr timing/resource report.
