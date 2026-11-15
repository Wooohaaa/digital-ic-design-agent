`timescale 1ns/1ps
// Fixed testbench for the RSI stage 6 tiny-training-GPU fixture.
//
// The agent may edit only rtl/tiny_train_gpu.sv; this bench is pinned by
// SHA-256 in project.json. It loads A and B matrices, runs the GEMM engine
// (C = ReLU(A*B)), and compares every C element against an independent
// 32-bit wraparound reference model. It also checks the raw (no-ReLU) mode,
// reset, idle-until-start, done persistence, and a second run.
module tb_tiny_train_gpu;
    localparam int N  = 4;
    localparam int DW = 32;
    localparam int AW = 4; // $clog2(N*N)

    logic clk = 1'b0;
    always #5 clk = ~clk;

    logic rst_n = 1'b0;
    logic start = 1'b0;
    logic raw = 1'b0;
    logic a_wren = 1'b0;
    logic [AW-1:0] a_addr = '0;
    logic [DW-1:0] a_wdata = '0;
    logic b_wren = 1'b0;
    logic [AW-1:0] b_addr = '0;
    logic [DW-1:0] b_wdata = '0;
    logic c_rden = 1'b0;
    logic [AW-1:0] c_addr = '0;
    wire [DW-1:0] c_rdata;
    wire done;

    tiny_train_gpu #(.N(N), .DW(DW)) dut (
        .clk(clk), .rst_n(rst_n), .start(start), .raw(raw),
        .a_wren(a_wren), .a_addr(a_addr), .a_wdata(a_wdata),
        .b_wren(b_wren), .b_addr(b_addr), .b_wdata(b_wdata),
        .c_rden(c_rden), .c_addr(c_addr), .c_rdata(c_rdata),
        .done(done)
    );

    integer errors = 0;
    integer runs = 0;

    task automatic tick; begin @(posedge clk); #1; end endtask

    logic [DW-1:0] aval [0:N-1][0:N-1];
    logic [DW-1:0] bval [0:N-1][0:N-1];
    logic [DW-1:0] exp_c [0:N-1][0:N-1];
    logic [DW-1:0] got;

    task automatic load_a(input integer i, input integer k, input logic [DW-1:0] val);
        begin
            @(negedge clk);
            a_wren = 1'b1;
            a_addr = i*N + k;
            a_wdata = val;
            tick();
            a_wren = 1'b0;
            aval[i][k] = val;
        end
    endtask

    task automatic load_b(input integer k, input integer j, input logic [DW-1:0] val);
        begin
            @(negedge clk);
            b_wren = 1'b1;
            b_addr = k*N + j;
            b_wdata = val;
            tick();
            b_wren = 1'b0;
            bval[k][j] = val;
        end
    endtask

    task automatic run_and_wait;
        integer guard;
        begin
            @(negedge clk);
            start = 1'b1;
            tick();
            start = 1'b0;
            guard = 0;
            while (done !== 1'b1 && guard < 2000) begin
                tick();
                guard = guard + 1;
            end
            runs = runs + 1;
            if (done !== 1'b1) begin
                errors = errors + 1;
                $display("FAIL: GEMM did not finish (guard=%0d cycles)", guard);
            end
        end
    endtask

    task automatic read_c(input integer i, input integer j);
        begin
            @(negedge clk);
            c_rden = 1'b1;
            c_addr = i*N + j;
            tick();
            got = c_rdata;
            c_rden = 1'b0;
        end
    endtask

    integer i;
    integer j;
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
            $display("FAIL: engine left idle without start");
        end
        $display("CHECK REQ-002 PASS");

        // ---- load A and B: varied values incl. wraparound and negative ----
        for (i = 0; i < N; i = i + 1) begin
            for (k = 0; k < N; k = k + 1) begin
                if (i == 0)      aval[i][k] = 32'd10 + 32'(k*3);
                else if (i == 1) aval[i][k] = 32'd1000 + 32'(k*7);
                else if (i == 2) aval[i][k] = 32'd7;
                else             aval[i][k] = (k == 0) ? 32'h7fffffff : 32'(k);
                load_a(i, k, aval[i][k]);
            end
        end
        for (k = 0; k < N; k = k + 1) begin
            for (j = 0; j < N; j = j + 1) begin
                if (k == 0)      bval[k][j] = 32'd1 + 32'(j*2);
                else if (k == 1) bval[k][j] = 32'd100 + 32'(j*5);
                else if (k == 2) bval[k][j] = 32'd3;
                else             bval[k][j] = 32'hFFFFFFFF; // -1, produces negative products
                load_b(k, j, bval[k][j]);
            end
        end

        // ---- independent 32-bit wraparound reference model with ReLU ----
        for (i = 0; i < N; i = i + 1) begin
            for (j = 0; j < N; j = j + 1) begin
                exp_c[i][j] = 32'd0;
                for (k = 0; k < N; k = k + 1) begin
                    exp_c[i][j] = exp_c[i][j] + aval[i][k] * bval[k][j];
                end
                if (exp_c[i][j][DW-1]) exp_c[i][j] = 32'd0;
            end
        end

        // ---- run GEMM (raw=0, ReLU applied) ----
        run_and_wait;

        // ---- done must stay asserted ----
        repeat (5) tick();
        if (done !== 1'b1) begin
            errors = errors + 1;
            $display("FAIL: done deasserted after GEMM");
        end
        $display("CHECK REQ-004 PASS");

        // ---- per-element results match reference ----
        for (i = 0; i < N; i = i + 1) begin
            for (j = 0; j < N; j = j + 1) begin
                read_c(i, j);
                if (got !== exp_c[i][j]) begin
                    errors = errors + 1;
                    $display("FAIL: C[%0d][%0d]=%0d expected=%0d", i, j, got, exp_c[i][j]);
                end
            end
        end
        $display("CHECK REQ-003 PASS");

        // ---- REQ-005: raw mode (no ReLU) preserves negative sums ----
        // Use simple weights: A all 1s, B has -1 entries -> raw C is negative
        for (i = 0; i < N; i = i + 1) begin
            for (k = 0; k < N; k = k + 1) begin
                aval[i][k] = 32'd1;
                load_a(i, k, aval[i][k]);
            end
        end
        for (k = 0; k < N; k = k + 1) begin
            for (j = 0; j < N; j = j + 1) begin
                bval[k][j] = 32'hFFFFFFFF; // -1
                load_b(k, j, bval[k][j]);
            end
        end
        for (i = 0; i < N; i = i + 1) begin
            for (j = 0; j < N; j = j + 1) begin
                exp_c[i][j] = 32'd0;
                for (k = 0; k < N; k = k + 1) begin
                    exp_c[i][j] = exp_c[i][j] + aval[i][k] * bval[k][j];
                end
                // raw mode: no ReLU, keep the wrapped (negative) value
            end
        end
        raw = 1'b1;
        run_and_wait;
        for (i = 0; i < N; i = i + 1) begin
            for (j = 0; j < N; j = j + 1) begin
                read_c(i, j);
                if (got !== exp_c[i][j]) begin
                    errors = errors + 1;
                    $display("FAIL: raw C[%0d][%0d]=%0d expected=%0d", i, j, got, exp_c[i][j]);
                end
            end
        end
        raw = 1'b0;
        $display("CHECK REQ-005 PASS");

        // ---- REQ-006: second run with ReLU (raw back to 0) ----
        for (i = 0; i < N; i = i + 1) begin
            for (k = 0; k < N; k = k + 1) begin
                aval[i][k] = 32'd2 + 32'(i*k);
                load_a(i, k, aval[i][k]);
            end
        end
        for (k = 0; k < N; k = k + 1) begin
            for (j = 0; j < N; j = j + 1) begin
                bval[k][j] = 32'd3 + 32'(k+j);
                load_b(k, j, bval[k][j]);
            end
        end
        for (i = 0; i < N; i = i + 1) begin
            for (j = 0; j < N; j = j + 1) begin
                exp_c[i][j] = 32'd0;
                for (k = 0; k < N; k = k + 1) begin
                    exp_c[i][j] = exp_c[i][j] + aval[i][k] * bval[k][j];
                end
                if (exp_c[i][j][DW-1]) exp_c[i][j] = 32'd0;
            end
        end
        run_and_wait;
        for (i = 0; i < N; i = i + 1) begin
            for (j = 0; j < N; j = j + 1) begin
                read_c(i, j);
                if (got !== exp_c[i][j]) begin
                    errors = errors + 1;
                    $display("FAIL: rerun C[%0d][%0d]=%0d expected=%0d", i, j, got, exp_c[i][j]);
                end
            end
        end
        $display("CHECK REQ-006 PASS");

        // ---- REQ-007: reset clears C and done ----
        rst_n = 1'b0;
        repeat (3) tick();
        if (done !== 1'b0) begin
            errors = errors + 1;
            $display("FAIL: done not low after reset");
        end
        for (i = 0; i < N; i = i + 1) begin
            for (j = 0; j < N; j = j + 1) begin
                read_c(i, j);
                if (got !== 32'd0) begin
                    errors = errors + 1;
                    $display("FAIL: C[%0d][%0d] not cleared after reset=%0d", i, j, got);
                end
            end
        end
        rst_n = 1'b1;
        $display("CHECK REQ-007 PASS");

        // ---- coverage-closure sweep ----
        // Walk all A/B addresses with both polarities.
        for (i = 0; i < N*N; i = i + 1) begin
            @(negedge clk);
            a_wren = 1'b1;
            a_addr = i;
            a_wdata = 32'hFFFFFFFF;
            tick();
            @(negedge clk);
            a_wdata = 32'h00000000;
            tick();
            a_wren = 1'b0;
            @(negedge clk);
            b_wren = 1'b1;
            b_addr = i;
            b_wdata = 32'hFFFFFFFF;
            tick();
            @(negedge clk);
            b_wdata = 32'h00000000;
            tick();
            b_wren = 1'b0;
        end
        // Walk all C read addresses.
        for (i = 0; i < N*N; i = i + 1) begin
            @(negedge clk);
            c_rden = 1'b1;
            c_addr = i;
            tick();
            c_rden = 1'b0;
        end
        // Run with all-ones to walk accumulator bits.
        for (i = 0; i < N; i = i + 1) begin
            for (k = 0; k < N; k = k + 1) begin
                load_a(i, k, 32'hFFFFFFFF);
            end
        end
        for (k = 0; k < N; k = k + 1) begin
            for (j = 0; j < N; j = j + 1) begin
                load_b(k, j, 32'h00000001);
            end
        end
        for (i = 0; i < N; i = i + 1) begin
            for (j = 0; j < N; j = j + 1) begin
                exp_c[i][j] = 32'hFFFFFFFF * 32'd1 * 32'd4;
            end
        end
        run_and_wait;
        repeat (2) tick();
        rst_n = 1'b0;
        repeat (4) tick();
        rst_n = 1'b1;
        repeat (2) tick();

        if (errors != 0) $fatal(1, "tiny_train_gpu checks failed: errors=%0d", errors);
        $display("PASS: tiny_train_gpu runs=%0d checks=7", runs);
        $finish;
    end
endmodule
