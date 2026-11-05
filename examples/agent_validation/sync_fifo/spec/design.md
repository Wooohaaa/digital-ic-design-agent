# Synchronous FIFO interface and behavior

Module: `sync_fifo #(DATA_WIDTH=8, DEPTH=4)`.

Ports: `clk`, synchronous active-low `rst_n`, `wr_en`, `rd_en`, `din[DATA_WIDTH-1:0]`, `dout[DATA_WIDTH-1:0]`, `full`, `empty`.

On a rising clock edge with reset asserted, clear occupancy and both pointers, assert `empty`, deassert `full`, and clear `dout`. A write is accepted only when `wr_en && !full` was true before that edge. A read is accepted only when `rd_en && !empty` was true before that edge. `dout` changes only on an accepted read and returns the oldest stored word. Rejected writes and reads have no effect.

For simultaneous read and write, evaluate both enables against pre-edge flags. When neither boundary blocks the operation, both complete and occupancy is unchanged. When empty, a simultaneous request accepts only the write. When full, it accepts only the read; the write is rejected for that edge. Do not add first-word fall-through behavior. `full` and `empty` must reflect occupancy after every edge. Support `DEPTH >= 2`; pointer wrap must work for the acceptance depth.

No board pins are assigned in this synthesis fixture. Keep the implementation synchronous and synthesizable; do not infer latches or use gated clocks.
