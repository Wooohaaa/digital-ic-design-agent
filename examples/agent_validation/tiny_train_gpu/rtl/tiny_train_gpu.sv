// tiny_train_gpu.sv - RSI roadmap stage 6: minimal training-GPU compute core.
//
// A GEMM engine (C = ReLU(A * B)) with on-chip weight/activation SRAM and a
// host load/read port. This is the forward-pass compute core of a training
// GPU; the backward pass and weight update happen on the host (see
// fpga_agent/training.py). No warp scheduler, no cache, no divergent control.
//
// Ports:
//   clk, rst_n, start, raw
//   a_wren/a_addr/a_wdata: host load matrix A (row-major, address = i*N + k)
//   b_wren/b_addr/b_wdata: host load matrix B (row-major, address = k*N + j)
//   c_rden/c_addr/c_rdata: host read result C (address = i*N + j)
//   raw: when 1, store new_acc directly (no ReLU); when 0, apply ReLU
//   done: asserts after N^3 cycles, stays until next start/reset
`timescale 1ns/1ps
module tiny_train_gpu #(
    parameter int unsigned N = 4,
    parameter int unsigned DW = 32
) (
    input  logic                               clk,
    input  logic                               rst_n,
    input  logic                               start,
    input  logic                               raw,
    input  logic                               a_wren,
    input  logic [$clog2(N*N)-1:0]             a_addr,
    input  logic [DW-1:0]                      a_wdata,
    input  logic                               b_wren,
    input  logic [$clog2(N*N)-1:0]             b_addr,
    input  logic [DW-1:0]                      b_wdata,
    input  logic                               c_rden,
    input  logic [$clog2(N*N)-1:0]             c_addr,
    output logic [DW-1:0]                      c_rdata,
    output logic                               done
);
    localparam int unsigned IW = (N > 1) ? $clog2(N) : 1;
    localparam int unsigned AW = $clog2(N*N);

    // ---- on-chip SRAM: A, B (host-loaded), C (DUT-written) ----
    logic [DW-1:0] amem [0:N*N-1];
    logic [DW-1:0] bmem [0:N*N-1];
    logic [DW-1:0] cmem [0:N*N-1];

    always_ff @(posedge clk) begin
        if (a_wren && !running) amem[a_addr] <= a_wdata;
    end
    always_ff @(posedge clk) begin
        if (b_wren && !running) bmem[b_addr] <= b_wdata;
    end
    // C memory: cleared on reset (stale results must not persist), written by
    // the GEMM loop, read by the host while idle.
    integer ci;
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            for (ci = 0; ci < N*N; ci = ci + 1) cmem[ci] <= {DW{1'b0}};
        end else if (running && idx_k == IW'(N-1)) begin
            cmem[c_idx] <= store_val;
        end
    end
    always_comb c_rdata = c_rden ? cmem[c_addr] : {DW{1'b0}};

    // ---- streaming control: triple-nested loop over i, j, k ----
    logic [IW-1:0] idx_i;
    logic [IW-1:0] idx_j;
    logic [IW-1:0] idx_k;
    logic [DW-1:0] acc;
    logic          running;

    // Decoded memory indices (hoisted for Icarus/Verilator compatibility).
    wire [AW-1:0] a_idx = AW'(idx_i) * AW'(N) + AW'(idx_k);
    wire [AW-1:0] b_idx = AW'(idx_k) * AW'(N) + AW'(idx_j);
    wire [AW-1:0] c_idx = AW'(idx_i) * AW'(N) + AW'(idx_j);

    wire [DW-1:0] a_val   = amem[a_idx];
    wire [DW-1:0] b_val   = bmem[b_idx];
    wire [DW-1:0] product = a_val * b_val;
    wire [DW-1:0] new_acc = acc + product;
    // ReLU: a 32-bit sum whose sign bit is set is treated as negative -> store 0.
    // (Unsigned `new_acc > 0` would never clamp; test the sign bit instead.)
    wire [DW-1:0] store_val = raw ? new_acc
                                  : ((new_acc[DW-1]) ? {DW{1'b0}} : new_acc);

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            idx_i   <= {IW{1'b0}};
            idx_j   <= {IW{1'b0}};
            idx_k   <= {IW{1'b0}};
            acc     <= {DW{1'b0}};
            running <= 1'b0;
            done    <= 1'b0;
        end else if (start && !running) begin
            idx_i   <= {IW{1'b0}};
            idx_j   <= {IW{1'b0}};
            idx_k   <= {IW{1'b0}};
            acc     <= {DW{1'b0}};
            running <= 1'b1;
            done    <= 1'b0;
        end else if (running) begin
            if (idx_k == IW'(N-1)) begin
                cmem[c_idx] <= store_val;
                idx_k <= {IW{1'b0}};
                acc   <= {DW{1'b0}};
                if (idx_j == IW'(N-1)) begin
                    if (idx_i == IW'(N-1)) begin
                        running <= 1'b0;
                        done    <= 1'b1;
                    end else begin
                        idx_i <= idx_i + 1'b1;
                        idx_j <= {IW{1'b0}};
                    end
                end else begin
                    idx_j <= idx_j + 1'b1;
                end
            end else begin
                acc   <= new_acc;
                idx_k <= idx_k + 1'b1;
            end
        end
    end
endmodule
