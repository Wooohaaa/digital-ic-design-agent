# SystemVerilog datapath interface and behavior

Module: `sv_datapath #(DATA_WIDTH=8, DEPTH=4)`.

Ports: `clk`, synchronous active-low `rst_n`, `load`, `start`, `shift_en`, `din[DATA_WIDTH-1:0]`, `sum[DATA_WIDTH-1:0]`, `shift_out[DATA_WIDTH-1:0]`, `busy`.

Reset (synchronous, active-low) clears the enum FSM to idle, clears the latched `beat` struct, the accumulator, the run index and `sum`, and clears the `generate`-built shift register.

A rising edge with `load` asserts latches `din` into the registered `beat` struct and marks it valid. A rising edge with `start` while a valid beat is held enters the run state; over the next `DEPTH` cycles the accumulator adds the held beat each cycle and `sum` becomes the total of `DEPTH` beats at the end of the run. `busy` is high only while running. The FSM returns to idle for one cycle after the sum is captured.

The shift register shifts `din` on every `shift_en` edge and exposes the last stage on `shift_out`; it is built with a `generate` loop so `DEPTH` is a real elaboration-time parameter. The run index width is `$clog2(DEPTH)`.

Keep the implementation synthesizable and vendor-neutral: no vendor primitives, no inferred latches, no gated clocks, and no board pins (this is a simulation/synthesis fixture).
