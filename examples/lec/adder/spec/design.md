# Design boundary

Pure combinational addition with explicit operand extension. No state, reset,
clocks, assumptions, memories, black boxes, or physical libraries. The
formal_equivalence.required flag prevents artifact readiness without current
LEC evidence. Exhaustive simulation verifies the mathematical contract;
LEC verifies that generic-cell synthesis preserves the accepted RTL.
