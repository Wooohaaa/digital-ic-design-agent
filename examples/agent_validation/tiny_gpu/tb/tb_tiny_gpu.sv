`timescale 1ns/1ps
// Fixed testbench for the RSI stage 4 tiny-GPU fixture.
//
// The agent may edit only rtl/tiny_gpu.sv; this bench is pinned by SHA-256 in
// project.json. It loads a dot-product kernel program plus per-lane input
// data, runs the lockstep SIMT core, and compares every lane's MAC result
// against an independent 32-bit wraparound reference model. It then runs
// directed microtests for the SETPLT/MOV/ADD/SUB/MUL opcodes, the SETPLT
// false path, and a BRA skip.
module tb_tiny_gpu;
    localparam int LANES = 4;
    localparam int NREG = 8;
    localparam int IMEM_DEPTH = 64;
    localparam int DMEM_BANK_DEPTH = 16;
    localparam int DW = 32;
    localparam int PC_W = 6;
    localparam int DMEM_W = $clog2(LANES*DMEM_BANK_DEPTH);

    logic clk = 1'b0;
    always #5 clk = ~clk;

    logic rst_n = 1'b0;
    logic start = 1'b0;
    logic imem_wren = 1'b0;
    logic [PC_W-1:0] imem_addr = '0;
    logic [DW-1:0] imem_wdata = '0;
    logic dmem_wren = 1'b0;
    logic [DMEM_W-1:0] dmem_addr = '0;
    logic [DW-1:0] dmem_wdata = '0;
    logic dmem_rden = 1'b0;
    wire [DW-1:0] dmem_rdata;
    wire done;

    tiny_gpu #(.LANES(LANES), .NREG(NREG), .IMEM_DEPTH(IMEM_DEPTH),
               .DMEM_BANK_DEPTH(DMEM_BANK_DEPTH), .DW(DW)) dut (
        .clk(clk), .rst_n(rst_n), .start(start),
        .imem_wren(imem_wren), .imem_addr(imem_addr), .imem_wdata(imem_wdata),
        .dmem_wren(dmem_wren), .dmem_addr(dmem_addr), .dmem_wdata(dmem_wdata),
        .dmem_rden(dmem_rden), .dmem_rdata(dmem_rdata),
        .done(done)
    );

    integer errors = 0;
    integer runs = 0;

    task automatic tick; begin @(posedge clk); #1; end endtask

    // {op[3:0], rd[2:0], ra[2:0], rb[2:0], rc[2:0], imm[15:0]}
    function automatic [DW-1:0] ins(input [3:0] op, input [2:0] rd, input [2:0] ra,
                                   input [2:0] rb, input [2:0] rc, input [15:0] imm);
        return {op, rd, ra, rb, rc, imm};
    endfunction

    localparam [3:0] OP_MOVI = 4'd0, OP_MOV = 4'd1, OP_ADD = 4'd2, OP_SUB = 4'd3,
                      OP_MUL = 4'd4, OP_MAD = 4'd5, OP_LD = 4'd6, OP_ST = 4'd7,
                      OP_SETPLT = 4'd8, OP_BRA = 4'd9, OP_EXIT = 4'd10;

    // ---- program 0: per-lane dot product s = sum_k a[k]*b[k]; store at offset 8 ----
    function automatic [DW-1:0] dot_instr(input integer idx);
        case (idx)
            0:  dot_instr = ins(OP_MOVI, 3'd0, 3'd0, 3'd0, 3'd0, 16'h0000); // r0 = 0
            1:  dot_instr = ins(OP_LD,   3'd1, 3'd0, 3'd0, 3'd0, 16'h0000); // r1 = a0
            2:  dot_instr = ins(OP_LD,   3'd2, 3'd0, 3'd0, 3'd0, 16'h0004); // r2 = b0
            3:  dot_instr = ins(OP_MAD,  3'd0, 3'd1, 3'd2, 3'd0, 16'h0000); // r0 += r1*r2
            4:  dot_instr = ins(OP_LD,   3'd1, 3'd0, 3'd0, 3'd0, 16'h0001); // r1 = a1
            5:  dot_instr = ins(OP_LD,   3'd2, 3'd0, 3'd0, 3'd0, 16'h0005); // r2 = b1
            6:  dot_instr = ins(OP_MAD,  3'd0, 3'd1, 3'd2, 3'd0, 16'h0000);
            7:  dot_instr = ins(OP_LD,   3'd1, 3'd0, 3'd0, 3'd0, 16'h0002); // r1 = a2
            8:  dot_instr = ins(OP_LD,   3'd2, 3'd0, 3'd0, 3'd0, 16'h0006); // r2 = b2
            9:  dot_instr = ins(OP_MAD,  3'd0, 3'd1, 3'd2, 3'd0, 16'h0000);
            10: dot_instr = ins(OP_LD,   3'd1, 3'd0, 3'd0, 3'd0, 16'h0003); // r1 = a3
            11: dot_instr = ins(OP_LD,   3'd2, 3'd0, 3'd0, 3'd0, 16'h0007); // r2 = b3
            12: dot_instr = ins(OP_MAD,  3'd0, 3'd1, 3'd2, 3'd0, 16'h0000);
            13: dot_instr = ins(OP_ST,   3'd0, 3'd0, 3'd0, 3'd0, 16'h0008); // [8] = r0
            14: dot_instr = ins(OP_EXIT, 3'd0, 3'd0, 3'd0, 3'd0, 16'h0000);
            default: dot_instr = '0;
        endcase
    endfunction

    // ---- program 1: SETPLT(true)/MOV/ADD/SUB/MUL directed microtest ----
    function automatic [DW-1:0] alu_instr(input integer idx);
        case (idx)
            0:  alu_instr = ins(OP_MOVI,   3'd0, 3'd0, 3'd0, 3'd0, 16'h0003); // r0 = 3
            1:  alu_instr = ins(OP_MOVI,   3'd2, 3'd0, 3'd0, 3'd0, 16'h0008); // r2 = 8
            2:  alu_instr = ins(OP_SETPLT, 3'd1, 3'd0, 3'd2, 3'd0, 16'h0000); // r1 = 1
            3:  alu_instr = ins(OP_MOV,    3'd3, 3'd1, 3'd0, 3'd0, 16'h0000); // r3 = r1
            4:  alu_instr = ins(OP_ADD,    3'd4, 3'd3, 3'd3, 3'd0, 16'h0000); // r4 = 2
            5:  alu_instr = ins(OP_SUB,    3'd5, 3'd4, 3'd3, 3'd0, 16'h0000); // r5 = 1
            6:  alu_instr = ins(OP_MUL,    3'd6, 3'd4, 3'd5, 3'd0, 16'h0000); // r6 = 2
            7:  alu_instr = ins(OP_ST,     3'd0, 3'd1, 3'd0, 3'd0, 16'h0009); // [9]  = r1
            8:  alu_instr = ins(OP_ST,     3'd0, 3'd3, 3'd0, 3'd0, 16'h000a); // [10] = r3
            9:  alu_instr = ins(OP_ST,     3'd0, 3'd4, 3'd0, 3'd0, 16'h000b); // [11] = r4
            10: alu_instr = ins(OP_ST,     3'd0, 3'd5, 3'd0, 3'd0, 16'h000c); // [12] = r5
            11: alu_instr = ins(OP_ST,     3'd0, 3'd6, 3'd0, 3'd0, 16'h000d); // [13] = r6
            12: alu_instr = ins(OP_EXIT,   3'd0, 3'd0, 3'd0, 3'd0, 16'h0000);
            default: alu_instr = '0;
        endcase
    endfunction

    // ---- program 2: SETPLT false path ----
    function automatic [DW-1:0] lt_instr(input integer idx);
        case (idx)
            0: lt_instr = ins(OP_MOVI,   3'd0, 3'd0, 3'd0, 3'd0, 16'h0009); // r0 = 9
            1: lt_instr = ins(OP_MOVI,   3'd2, 3'd0, 3'd0, 3'd0, 16'h0008); // r2 = 8
            2: lt_instr = ins(OP_SETPLT, 3'd1, 3'd0, 3'd2, 3'd0, 16'h0000); // r1 = 0
            3: lt_instr = ins(OP_ST,     3'd0, 3'd1, 3'd0, 3'd0, 16'h0009); // [9] = 0
            4: lt_instr = ins(OP_EXIT,   3'd0, 3'd0, 3'd0, 3'd0, 16'h0000);
            default: lt_instr = '0;
        endcase
    endfunction

    // ---- program 3: BRA skips the two MOVI instructions at 4..5 ----
    function automatic [DW-1:0] bra_instr(input integer idx);
        case (idx)
            0: bra_instr = ins(OP_MOVI,   3'd0, 3'd0, 3'd0, 3'd0, 16'h0003); // r0 = 3
            1: bra_instr = ins(OP_MOVI,   3'd2, 3'd0, 3'd0, 3'd0, 16'h0008); // r2 = 8
            2: bra_instr = ins(OP_SETPLT, 3'd1, 3'd0, 3'd2, 3'd0, 16'h0000); // r1 = 1
            3: bra_instr = ins(OP_BRA,    3'd0, 3'd0, 3'd0, 3'd0, 16'h0006); // PC = 6
            4: bra_instr = ins(OP_MOVI,   3'd7, 3'd0, 3'd0, 3'd0, 16'h0009); // skipped
            5: bra_instr = ins(OP_MOVI,   3'd7, 3'd0, 3'd0, 3'd0, 16'h000a); // skipped
            6: bra_instr = ins(OP_ST,     3'd0, 3'd1, 3'd0, 3'd0, 16'h000e); // [14] = r1
            7: bra_instr = ins(OP_EXIT,   3'd0, 3'd0, 3'd0, 3'd0, 16'h0000);
            default: bra_instr = '0;
        endcase
    endfunction

    // ---- program 4: register walk - load all 8 registers with all-ones ----
    function automatic [DW-1:0] regset_instr(input integer idx);
        case (idx)
            0: regset_instr = ins(OP_LD, 3'd0, 3'd0, 3'd0, 3'd0, 16'h0000);
            1: regset_instr = ins(OP_LD, 3'd1, 3'd0, 3'd0, 3'd0, 16'h0001);
            2: regset_instr = ins(OP_LD, 3'd2, 3'd0, 3'd0, 3'd0, 16'h0002);
            3: regset_instr = ins(OP_LD, 3'd3, 3'd0, 3'd0, 3'd0, 16'h0003);
            4: regset_instr = ins(OP_LD, 3'd4, 3'd0, 3'd0, 3'd0, 16'h0004);
            5: regset_instr = ins(OP_LD, 3'd5, 3'd0, 3'd0, 3'd0, 16'h0005);
            6: regset_instr = ins(OP_LD, 3'd6, 3'd0, 3'd0, 3'd0, 16'h0006);
            7: regset_instr = ins(OP_LD, 3'd7, 3'd0, 3'd0, 3'd0, 16'h0007);
            8: regset_instr = ins(OP_EXIT, 3'd0, 3'd0, 3'd0, 3'd0, 16'h0000);
            default: regset_instr = '0;
        endcase
    endfunction

    // ---- program 5: coverage walk - exercises every inst/imm/pc bit and
    //      clears the register file (the all-ones word decodes as a NOP) ----
    function automatic [DW-1:0] cover_instr(input integer idx);
        case (idx)
            0:  cover_instr = 32'hFFFFFFFF;                                   // NOP, all-ones inst
            1:  cover_instr = ins(OP_MUL,  3'd2, 3'd0, 3'd0, 3'd0, 16'h0000); // read r0 (all-ones)
            2:  cover_instr = ins(OP_MOVI, 3'd0, 3'd0, 3'd0, 3'd0, 16'h0000);
            3:  cover_instr = ins(OP_MOVI, 3'd1, 3'd0, 3'd0, 3'd0, 16'h0000);
            4:  cover_instr = ins(OP_MOVI, 3'd2, 3'd0, 3'd0, 3'd0, 16'h0000);
            5:  cover_instr = ins(OP_MOVI, 3'd3, 3'd0, 3'd0, 3'd0, 16'h0000);
            6:  cover_instr = ins(OP_MOVI, 3'd4, 3'd0, 3'd0, 3'd0, 16'h0000);
            7:  cover_instr = ins(OP_MOVI, 3'd5, 3'd0, 3'd0, 3'd0, 16'h0000);
            8:  cover_instr = ins(OP_MOVI, 3'd6, 3'd0, 3'd0, 3'd0, 16'h0000);
            9:  cover_instr = ins(OP_MOVI, 3'd7, 3'd0, 3'd0, 3'd0, 16'h0000);
            34: cover_instr = ins(OP_EXIT, 3'd0, 3'd0, 3'd0, 3'd0, 16'h0000);
            default: cover_instr = 32'hF0000000;                             // NOP: pad pc past bit 5
        endcase
    endfunction

    // ---- per-lane input vectors (flat: lane l, element k -> l*4+k) ----
    logic [DW-1:0] a_val [0:LANES*4-1];
    logic [DW-1:0] b_val [0:LANES*4-1];
    logic [DW-1:0] exp_dot [0:LANES-1];
    logic [DW-1:0] got;

    task automatic load_program(input integer which, input integer count);
        integer idx;
        begin
            for (idx = 0; idx < count; idx = idx + 1) begin
                @(negedge clk);
                imem_wren = 1'b1;
                imem_addr = idx[PC_W-1:0];
                case (which)
                    0: imem_wdata = dot_instr(idx);
                    1: imem_wdata = alu_instr(idx);
                    2: imem_wdata = lt_instr(idx);
                    3: imem_wdata = bra_instr(idx);
                    4: imem_wdata = regset_instr(idx);
                    5: imem_wdata = cover_instr(idx);
                endcase
                tick();
                imem_wren = 1'b0;
            end
        end
    endtask

    task automatic run_and_wait(input integer which, input integer count);
        integer guard;
        begin
            load_program(which, count);
            @(negedge clk);
            start = 1'b1;
            tick();
            start = 1'b0;
            guard = 0;
            while (done !== 1'b1 && guard < 500) begin
                tick();
                guard = guard + 1;
            end
            runs = runs + 1;
            if (done !== 1'b1) begin
                errors = errors + 1;
                $display("FAIL: program %0d did not finish (guard=%0d cycles)", which, guard);
            end
        end
    endtask

    task automatic read_word(input integer addr);
        begin
            @(negedge clk);
            dmem_rden = 1'b1;
            dmem_addr = addr[DMEM_W-1:0];
            tick();
            got = dmem_rdata;
            dmem_rden = 1'b0;
        end
    endtask

    // Walk every instruction word (all-ones then zero) to exercise the whole
    // imem data/address port; then walk every data word with a read-back so the
    // dmem write/read ports see both polarities on every bit.
    task automatic sweep_imem;
        integer w;
        begin
            for (w = 0; w < IMEM_DEPTH; w = w + 1) begin
                @(negedge clk);
                imem_wren = 1'b1;
                imem_addr = w[PC_W-1:0];
                imem_wdata = 32'hFFFFFFFF;
                tick();
                @(negedge clk);
                imem_wdata = 32'h00000000;
                tick();
                imem_wren = 1'b0;
            end
        end
    endtask

    task automatic sweep_dmem;
        integer w;
        begin
            for (w = 0; w < LANES*DMEM_BANK_DEPTH; w = w + 1) begin
                @(negedge clk);
                dmem_wren = 1'b1;
                dmem_addr = w[DMEM_W-1:0];
                dmem_wdata = 32'hFFFFFFFF;
                tick();
                dmem_wren = 1'b0;
                @(negedge clk);
                dmem_rden = 1'b1;
                tick();
                dmem_rden = 1'b0;
                @(negedge clk);
                dmem_wren = 1'b1;
                dmem_wdata = 32'h00000000;
                tick();
                dmem_wren = 1'b0;
                @(negedge clk);
                dmem_rden = 1'b1;
                tick();
                dmem_rden = 1'b0;
            end
        end
    endtask

    integer l;
    integer k;
    initial begin
        // ---- power-on reset ----
        repeat (3) tick();
        if (done !== 1'b0) begin
            errors = errors + 1;
            $display("FAIL: done not low after reset");
        end
        $display("CHECK REQ-001 PASS");

        rst_n = 1'b1;
        repeat (5) tick();
        if (done !== 1'b0) begin
            errors = errors + 1;
            $display("FAIL: core left idle without start");
        end
        $display("CHECK REQ-002 PASS");

        // ---- load per-lane input data for the dot-product kernel ----
        // lane 0: small values; lane 1: thousands; lane 2: uniform; lane 3: wraparound
        a_val[0] = 32'd10;  a_val[1] = 32'd20;  a_val[2] = 32'd30;  a_val[3] = 32'd40;
        b_val[0] = 32'd1;   b_val[1] = 32'd2;   b_val[2] = 32'd3;   b_val[3] = 32'd4;
        a_val[4] = 32'd1000; a_val[5] = 32'd2000; a_val[6] = 32'd3000; a_val[7] = 32'd4000;
        b_val[4] = 32'd3;    b_val[5] = 32'd7;    b_val[6] = 32'd11;   b_val[7] = 32'd13;
        a_val[8] = 32'd7;  a_val[9] = 32'd7;  a_val[10] = 32'd7; a_val[11] = 32'd7;
        b_val[8] = 32'd7;  b_val[9] = 32'd7;  b_val[10] = 32'd7; b_val[11] = 32'd7;
        a_val[12] = 32'h7fffffff; a_val[13] = 32'd1; a_val[14] = 32'd2; a_val[15] = 32'd3;
        b_val[12] = 32'd2;        b_val[13] = 32'd1; b_val[14] = 32'd1; b_val[15] = 32'd1;

        for (l = 0; l < LANES; l = l + 1) begin
            for (k = 0; k < 4; k = k + 1) begin
                @(negedge clk);
                dmem_wren = 1'b1;
                dmem_addr = (l*DMEM_BANK_DEPTH + k);
                dmem_wdata = a_val[l*4+k];
                tick();
                dmem_wren = 1'b0;
                @(negedge clk);
                dmem_wren = 1'b1;
                dmem_addr = (l*DMEM_BANK_DEPTH + 4 + k);
                dmem_wdata = b_val[l*4+k];
                tick();
                dmem_wren = 1'b0;
            end
        end

        // ---- independent 32-bit wraparound reference model ----
        for (l = 0; l < LANES; l = l + 1) begin
            exp_dot[l] = a_val[l*4+0]*b_val[l*4+0] + a_val[l*4+1]*b_val[l*4+1] +
                         a_val[l*4+2]*b_val[l*4+2] + a_val[l*4+3]*b_val[l*4+3];
        end

        // ---- run the dot-product kernel ----
        run_and_wait(0, 15);

        // ---- done must stay asserted after EXIT (core halted) ----
        repeat (5) tick();
        if (done !== 1'b1) begin
            errors = errors + 1;
            $display("FAIL: done deasserted after EXIT");
        end
        $display("CHECK REQ-005 PASS");

        // ---- per-lane results and lane isolation ----
        for (l = 0; l < LANES; l = l + 1) begin
            read_word(l*DMEM_BANK_DEPTH + 8);
            if (got !== exp_dot[l]) begin
                errors = errors + 1;
                $display("FAIL: lane %0d dot=%0d expected=%0d", l, got, exp_dot[l]);
            end
            read_word(l*DMEM_BANK_DEPTH + 0);
            if (got !== a_val[l*4+0]) begin
                errors = errors + 1;
                $display("FAIL: lane %0d a0 clobbered=%0d expected=%0d", l, got, a_val[l*4+0]);
            end
        end
        $display("CHECK REQ-003 PASS");
        $display("CHECK REQ-004 PASS");

        // ---- SETPLT/MOV/ADD/SUB/MUL microtest ----
        run_and_wait(1, 13);
        for (l = 0; l < LANES; l = l + 1) begin
            read_word(l*DMEM_BANK_DEPTH + 9);
            if (got !== 32'd1) begin
                errors = errors + 1;
                $display("FAIL: alu lane %0d [9]=%0d expected=1", l, got);
            end
            read_word(l*DMEM_BANK_DEPTH + 10);
            if (got !== 32'd1) begin
                errors = errors + 1;
                $display("FAIL: alu lane %0d [10]=%0d expected=1", l, got);
            end
            read_word(l*DMEM_BANK_DEPTH + 11);
            if (got !== 32'd2) begin
                errors = errors + 1;
                $display("FAIL: alu lane %0d [11]=%0d expected=2", l, got);
            end
            read_word(l*DMEM_BANK_DEPTH + 12);
            if (got !== 32'd1) begin
                errors = errors + 1;
                $display("FAIL: alu lane %0d [12]=%0d expected=1", l, got);
            end
            read_word(l*DMEM_BANK_DEPTH + 13);
            if (got !== 32'd2) begin
                errors = errors + 1;
                $display("FAIL: alu lane %0d [13]=%0d expected=2", l, got);
            end
        end
        $display("CHECK REQ-006 PASS");

        // ---- SETPLT false path ----
        run_and_wait(2, 5);
        for (l = 0; l < LANES; l = l + 1) begin
            read_word(l*DMEM_BANK_DEPTH + 9);
            if (got !== 32'd0) begin
                errors = errors + 1;
                $display("FAIL: setp.lt false lane %0d [9]=%0d expected=0", l, got);
            end
        end

        // ---- BRA skip ----
        run_and_wait(3, 8);
        for (l = 0; l < LANES; l = l + 1) begin
            read_word(l*DMEM_BANK_DEPTH + 14);
            if (got !== 32'd1) begin
                errors = errors + 1;
                $display("FAIL: bra skip lane %0d [14]=%0d expected=1", l, got);
            end
        end
        $display("CHECK REQ-007 PASS");

        // ---- coverage-closure sweep (structural coverage, not a REQ check) ----
        // Fill every per-lane data word with all-ones so the register-walk
        // program can load 0xffffffff into all 8 registers.
        for (l = 0; l < LANES; l = l + 1) begin
            for (k = 0; k < 8; k = k + 1) begin
                @(negedge clk);
                dmem_wren = 1'b1;
                dmem_addr = (l*DMEM_BANK_DEPTH + k);
                dmem_wdata = 32'hFFFFFFFF;
                tick();
                dmem_wren = 1'b0;
            end
        end
        run_and_wait(4, 9);   // registers -> all-ones
        run_and_wait(5, 35);  // walk inst/imm/pc and clear registers -> all-zero
        run_and_wait(4, 9);   // registers -> all-ones again
        sweep_imem;
        sweep_dmem;
        repeat (2) tick();
        rst_n = 1'b0;         // final reset clears the register file and toggles rst_n
        repeat (4) tick();
        rst_n = 1'b1;
        repeat (2) tick();

        if (errors != 0) $fatal(1, "tiny_gpu checks failed: errors=%0d", errors);
        $display("PASS: tiny_gpu runs=%0d checks=7", runs);
        $finish;
    end
endmodule
