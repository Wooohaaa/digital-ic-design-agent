`timescale 1ns/1ps
// 4-bit up-counter with functional cover points and a concurrent assertion.
//
// Stage 5 fixture: Verilator emits a `user` coverage point for every
// `cover property` and aborts the run (non-zero exit + "Assertion failed")
// when an `assert property` fires. The assertion checks reset behaviour
// (``!rst_n |-> cnt == 0``) so it is independent of the enable/count sampling
// order. The testbench drives `en` high long enough for the free-running
// counter to visit every cover value (0, 5, 15) and wrap.
module counter (
    input  logic clk,
    input  logic rst_n,
    input  logic en,
    output logic [3:0] cnt
);
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) cnt <= 4'd0;
        else if (en) cnt <= cnt + 1'b1;
    end

    // Functional cover points (Verilator `user` coverage points).
    cover property (@(posedge clk) cnt == 4'd0);
    cover property (@(posedge clk) cnt == 4'd5);
    cover property (@(posedge clk) cnt == 4'd15);
    cover property (@(posedge clk) en == 1'b1);

    // Concurrent assertion: while in reset the counter must read zero.
    assert property (@(posedge clk) !rst_n |-> cnt == 4'd0);
endmodule
