# Counter state-equivalence fixture

An 8-bit rising-edge counter synchronously resets to zero when rst is high,
increments modulo 256 when en is high, and otherwise holds its value.
State-bijection LEC must preserve all state bits and outputs for all input
values, including reset assertion and enable/hold cases. Simulation separately
checks rollover, repeated reset, and enable patterns over 2048 cycles.
