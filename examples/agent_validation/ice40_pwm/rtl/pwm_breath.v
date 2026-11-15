`timescale 1ns/1ps
// PWM "breathing" generator pinned as the vendor-neutral iCE40 fixture.
// A free-running counter drives a duty-cycle comparison and a coarse level
// nibble. Single clock, synchronous active-low reset.
module pwm_breath (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] duty,
    output reg        pwm,
    output reg  [3:0] level
);
    reg [7:0] counter;
    always @(posedge clk) begin
        if (!rst_n) counter <= 8'd0;
        else        counter <= counter + 8'd1;
    end

    always @(posedge clk) begin
        if (!rst_n)        pwm <= 1'b0;
        else if (counter < duty) pwm <= 1'b1;
        else               pwm <= 1'b0;
    end

    always @(posedge clk) begin
        if (!rst_n) level <= 4'd0;
        else        level <= counter[7:4];
    end
endmodule
