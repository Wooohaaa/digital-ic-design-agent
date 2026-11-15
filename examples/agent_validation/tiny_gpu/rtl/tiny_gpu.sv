// tiny_gpu.sv - a minimal, lockstep SIMT "tiny GPU" for the RSI roadmap stage 4
// (CUDA-compatible tiny GPU with a very small PTX-like instruction subset,
//  inference-only). It is a fixed-function MAC datapath, not a full GPU: no
// warp scheduler, no cache, no memory hierarchy, no divergent control flow.
//
// Instruction subset (mapped to the PTX names it resembles):
//   MOVI rd, imm     -> mov   (immediate)
//   MOV  rd, ra      -> mov
//   ADD  rd, ra, rb  -> add
//   SUB  rd, ra, rb  -> sub
//   MUL  rd, ra, rb  -> mul
//   MAD  rd, ra, rb  -> mad  (rd = ra * rb + rd, the inference MAC)
//   LD   rd, [imm]   -> ld   (per-lane data-memory load)
//   ST   ra, [imm]   -> st   (per-lane data-memory store)
//   SETPLT rd,ra,rb  -> setp.lt
//   BRA  imm         -> bra
//   EXIT             -> exit
// Encoding: {op[3:0], rd[2:0], ra[2:0], rb[2:0], rc[2:0], imm[15:0]}.
// LD/ST use imm[3:0] as the word offset inside the lane's own 16-word bank, so
// lane addressing is implicit: address = lane_index * DMEM_BANK_DEPTH + offset.
`timescale 1ns/1ps
module tiny_gpu #(
    parameter int unsigned LANES           = 4,
    parameter int unsigned NREG            = 8,
    parameter int unsigned IMEM_DEPTH      = 64,
    parameter int unsigned DMEM_BANK_DEPTH = 16,
    parameter int unsigned DW              = 32
) (
    input  logic                                      clk,
    input  logic                                      rst_n,
    input  logic                                      start,
    // Host instruction-memory load port (program is supplied by the host).
    input  logic                                      imem_wren,
    input  logic [((IMEM_DEPTH > 1) ? $clog2(IMEM_DEPTH) : 1) - 1:0] imem_addr,
    input  logic [DW-1:0]                             imem_wdata,
    // Host data-memory port; absolute address = lane * DMEM_BANK_DEPTH + word.
    input  logic                                      dmem_wren,
    input  logic [$clog2(LANES*DMEM_BANK_DEPTH)-1:0]  dmem_addr,
    input  logic [DW-1:0]                             dmem_wdata,
    input  logic                                      dmem_rden,
    output logic [DW-1:0]                             dmem_rdata,
    output logic                                      done
);
    localparam int unsigned PC_W   = (IMEM_DEPTH > 1) ? $clog2(IMEM_DEPTH) : 1;
    localparam int unsigned BANK_W = $clog2(DMEM_BANK_DEPTH);
    localparam int unsigned LCLOG  = $clog2(LANES);

    localparam logic [3:0] OP_MOVI   = 4'd0;
    localparam logic [3:0] OP_MOV    = 4'd1;
    localparam logic [3:0] OP_ADD    = 4'd2;
    localparam logic [3:0] OP_SUB    = 4'd3;
    localparam logic [3:0] OP_MUL    = 4'd4;
    localparam logic [3:0] OP_MAD    = 4'd5;
    localparam logic [3:0] OP_LD     = 4'd6;
    localparam logic [3:0] OP_ST     = 4'd7;
    localparam logic [3:0] OP_SETPLT = 4'd8;
    localparam logic [3:0] OP_BRA    = 4'd9;
    localparam logic [3:0] OP_EXIT   = 4'd10;

    // ---- instruction memory, loaded by the host over imem_* ----
    logic [DW-1:0] imem [0:IMEM_DEPTH-1];
    always_ff @(posedge clk) begin
        if (imem_wren) imem[imem_addr] <= imem_wdata;
    end

    // ---- data memory: one bank per lane ----
    logic [DW-1:0] dmem [0:LANES-1][0:DMEM_BANK_DEPTH-1];
    wire  [LCLOG-1:0]  bank_idx = dmem_addr[BANK_W+:LCLOG];
    wire  [BANK_W-1:0] word_idx = dmem_addr[BANK_W-1:0];
    always_ff @(posedge clk) begin
        if (dmem_wren && !running) dmem[bank_idx][word_idx] <= dmem_wdata;
    end
    always_comb dmem_rdata = dmem_rden ? dmem[bank_idx][word_idx] : {DW{1'b0}};

    // ---- shared front end: one program counter drives every lane ----
    logic [DW-1:0]   inst;
    logic [PC_W-1:0] pc;
    logic            running;

    // Decoded fields are hoisted into continuous assignments: Icarus does not
    // support constant part-selects inside always_* processes.
    wire [3:0]        op         = inst[31:28];
    wire [15:0]       imm        = inst[15:0];
    wire [PC_W-1:0]   bra_target = imm[5:0];
    wire [DW-1:0]     imem0      = imem[0];
    // rc (inst[18:16]) is reserved for future predicate sources and is not yet
    // consumed; mark it read so strict lint does not flag the encoding field.
    /* verilator lint_off UNUSEDSIGNAL */
    wire [2:0]        rsrc_c     = inst[18:16];
    /* verilator lint_on UNUSEDSIGNAL */

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            pc      <= {PC_W{1'b0}};
            inst    <= {DW{1'b0}};
            running <= 1'b0;
            done    <= 1'b0;
        end else if (start && !running) begin
            pc      <= {{(PC_W-1){1'b0}}, 1'b1};
            inst    <= imem0;
            running <= 1'b1;
            done    <= 1'b0;
        end else if (running) begin
            if (op == OP_EXIT) begin
                running <= 1'b0;
                done    <= 1'b1;
            end else begin
                inst <= imem[pc];
                pc   <= (op == OP_BRA) ? bra_target : pc + 1'b1;
            end
        end
    end

    // ---- per-lane datapath: private register file + MAC, lockstep ----
    genvar g;
    generate
        for (g = 0; g < LANES; g++) begin : g_lane
            logic [DW-1:0] rfile [0:NREG-1];
            logic [DW-1:0] ra_val;
            logic [DW-1:0] rb_val;
            wire  [2:0]    rd    = inst[27:25];
            wire  [2:0]    rsrc_a = inst[24:22];
            wire  [2:0]    rsrc_b = inst[21:19];
            wire  [BANK_W-1:0] lane_off = imm[BANK_W-1:0];
            integer        i;

            always_comb begin
                ra_val = rfile[rsrc_a];
                rb_val = rfile[rsrc_b];
            end

            always_ff @(posedge clk) begin
                if (!rst_n) begin
                    for (i = 0; i < NREG; i = i + 1) rfile[i] <= {DW{1'b0}};
                end else if (running) begin
                    case (op)
                        OP_MOVI:   rfile[rd] <= {{(DW-16){1'b0}}, imm};
                        OP_MOV:    rfile[rd] <= ra_val;
                        OP_ADD:    rfile[rd] <= ra_val + rb_val;
                        OP_SUB:    rfile[rd] <= ra_val - rb_val;
                        OP_MUL:    rfile[rd] <= ra_val * rb_val;
                        OP_MAD:    rfile[rd] <= ra_val * rb_val + rfile[rd];
                        OP_LD:     rfile[rd] <= dmem[g][lane_off];
                        OP_ST:     dmem[g][lane_off] <= ra_val;
                        OP_SETPLT: rfile[rd] <= (ra_val < rb_val) ? {{(DW-1){1'b0}}, 1'b1}
                                                                  : {DW{1'b0}};
                        default:   ; // BRA/EXIT/NOP: no register write
                    endcase
                end
            end
        end
    endgenerate
endmodule
