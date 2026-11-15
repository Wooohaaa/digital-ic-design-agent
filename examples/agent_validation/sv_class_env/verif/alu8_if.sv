`timescale 1ns/1ps
interface alu8_if (input logic clk);
    logic       rst_n;
    logic       en;
    logic [7:0] a;
    logic [7:0] b;
    logic [1:0] op;
    logic [7:0] y;
endinterface
