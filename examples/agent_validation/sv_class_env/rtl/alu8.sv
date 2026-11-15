`timescale 1ns/1ps
// Registered 8-bit ALU used to pin the minimal class-based (UVM-style)
// verification environment. Single clock, synchronous active-low reset.
module alu8 (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       en,
    input  logic [7:0] a,
    input  logic [7:0] b,
    input  logic [1:0] op,
    output logic [7:0] y
);
    logic [7:0] acc;
    always_ff @(posedge clk) begin
        if (!rst_n) acc <= 8'h00;
        else if (en) begin
            unique case (op)
                2'd0: acc <= a + b;
                2'd1: acc <= a - b;
                2'd2: acc <= a & b;
                default: acc <= a | b;
            endcase
        end
    end
    assign y = acc;
endmodule
