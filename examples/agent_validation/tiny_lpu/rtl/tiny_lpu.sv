// tiny_lpu.sv - RSI roadmap stage 5: minimal inference-only LPU
// (tensor streaming processor: on-chip weight SRAM, streaming activations,
//  parallel MAC units, deterministic execution. No warp scheduler, no cache,
//  no branching, no cache hierarchy - a fixed-function GEMV engine).
//
// Execution model:
//   The host loads NMAC banks of NW weights into on-chip SRAM. On `start`, the
//   engine streams NW activations through NMAC parallel MAC units. Each cycle
//   (when act_valid) every MAC accumulates acc[g] += wmem[g][idx] * act_in,
//   and idx advances. After NW activations, done asserts and the engine halts.
//   Deterministic: no scheduling, no divergence, fixed dataflow.
//
// Ports:
//   clk, rst_n, start
//   wmem_wren/wmem_addr/wmem_wdata: host weight-load port (absolute address =
//     g*NW + k; wmem_wren honored only when !running)
//   act_valid/act_in: streaming activation input
//   out[NMAC*DW-1:0]: accumulated dot products, out[g*DW +: DW] = MAC g result
//   done: asserts after NW weights consumed, stays until next start
`timescale 1ns/1ps
module tiny_lpu #(
    parameter int unsigned NMAC = 4,
    parameter int unsigned NW   = 16,
    parameter int unsigned DW   = 32
) (
    input  logic                               clk,
    input  logic                               rst_n,
    input  logic                               start,
    input  logic                               wmem_wren,
    input  logic [$clog2(NMAC*NW)-1:0]         wmem_addr,
    input  logic [DW-1:0]                      wmem_wdata,
    input  logic                               act_valid,
    input  logic [DW-1:0]                      act_in,
    output logic [NMAC*DW-1:0]                 out,
    output logic                               done
);
    localparam int unsigned IW = (NW > 1) ? $clog2(NW) : 1;
    localparam int unsigned BM = (NMAC > 1) ? $clog2(NMAC) : 1;

    // ---- on-chip weight SRAM: one bank per MAC unit ----
    logic [DW-1:0] wmem [0:NMAC-1][0:NW-1];
    wire [BM-1:0]  bank   = wmem_addr[IW+:BM];
    wire [IW-1:0]  word   = wmem_addr[IW-1:0];

    always_ff @(posedge clk) begin
        if (wmem_wren && !running) wmem[bank][word] <= wmem_wdata;
    end

    // ---- streaming control: shared index, lockstep ----
    logic [IW-1:0] idx;
    logic          running;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            idx     <= {IW{1'b0}};
            running <= 1'b0;
            done    <= 1'b0;
        end else if (start && !running) begin
            idx     <= {IW{1'b0}};
            running <= 1'b1;
            done    <= 1'b0;
        end else if (running && act_valid) begin
            if (idx == IW'(NW-1)) begin
                running <= 1'b0;
                done    <= 1'b1;
            end else begin
                idx <= idx + 1'b1;
            end
        end
    end

    // ---- MAC array: one accumulator per bank, all in lockstep ----
    genvar g;
    generate
        for (g = 0; g < NMAC; g++) begin : g_mac
            logic [DW-1:0] acc_g;
            always_ff @(posedge clk) begin
                if (!rst_n) begin
                    acc_g <= {DW{1'b0}};
                end else if (start && !running) begin
                    acc_g <= {DW{1'b0}};   // fresh computation per stream
                end else if (running && act_valid) begin
                    acc_g <= acc_g + wmem[g][idx] * act_in;
                end
            end
            assign out[g*DW +: DW] = acc_g;
        end
    endgenerate
endmodule
