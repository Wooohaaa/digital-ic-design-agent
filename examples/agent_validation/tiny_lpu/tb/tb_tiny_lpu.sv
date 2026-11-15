`timescale 1ns/1ps
// Fixed testbench for the RSI stage 5 tiny-LPU fixture.
//
// The agent may edit only rtl/tiny_lpu.sv; this bench is pinned by SHA-256 in
// project.json. It loads per-MAC weight banks, streams NW activations, and
// compares every MAC accumulator against an independent 32-bit wraparound
// reference model. It also checks reset, idle-until-start, done persistence,
// a second streaming run, and a paused/resumed stream.
module tb_tiny_lpu;
    localparam int NMAC = 4;
    localparam int NW   = 16;
    localparam int DW   = 32;
    localparam int AW   = 6; // $clog2(NMAC*NW)

    logic clk = 1'b0;
    always #5 clk = ~clk;

    logic rst_n = 1'b0;
    logic start = 1'b0;
    logic wmem_wren = 1'b0;
    logic [AW-1:0] wmem_addr = '0;
    logic [DW-1:0] wmem_wdata = '0;
    logic act_valid = 1'b0;
    logic [DW-1:0] act_in = '0;
    wire [NMAC*DW-1:0] out;
    wire done;

    tiny_lpu #(.NMAC(NMAC), .NW(NW), .DW(DW)) dut (
        .clk(clk), .rst_n(rst_n), .start(start),
        .wmem_wren(wmem_wren), .wmem_addr(wmem_addr), .wmem_wdata(wmem_wdata),
        .act_valid(act_valid), .act_in(act_in),
        .out(out), .done(done)
    );

    integer errors = 0;
    integer runs = 0;

    task automatic tick; begin @(posedge clk); #1; end endtask

    // ---- per-MAC weight banks and streaming activations ----
    logic [DW-1:0] wval [0:NMAC-1][0:NW-1];
    logic [DW-1:0] aval [0:NW-1];
    logic [DW-1:0] exp_out [0:NMAC-1];
    logic [DW-1:0] got;

    task automatic load_weight(input integer g, input integer k, input logic [DW-1:0] val);
        begin
            @(negedge clk);
            wmem_wren = 1'b1;
            wmem_addr = g*NW + k;
            wmem_wdata = val;
            tick();
            wmem_wren = 1'b0;
            wval[g][k] = val;
        end
    endtask

    task automatic run_and_wait;
        integer k;
        integer guard;
        begin
            for (k = 0; k < NW; k = k + 1) begin
                @(negedge clk);
                act_valid = 1'b1;
                act_in = aval[k];
                tick();
            end
            act_valid = 1'b0;
            guard = 0;
            while (done !== 1'b1 && guard < 500) begin
                tick();
                guard = guard + 1;
            end
            runs = runs + 1;
            if (done !== 1'b1) begin
                errors = errors + 1;
                $display("FAIL: LPU did not finish (guard=%0d cycles)", guard);
            end
        end
    endtask

    task automatic read_mac(input integer g);
        begin
            got = out[g*DW +: DW];
        end
    endtask

    integer g;
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

        // ---- load weights: bank 0 small, bank 1 thousands, bank 2 uniform,
        //      bank 3 wraparound ----
        for (g = 0; g < NMAC; g = g + 1) begin
            for (k = 0; k < NW; k = k + 1) begin
                if (g == 0)      wval[g][k] = 32'd10 + 32'(k*3);
                else if (g == 1) wval[g][k] = 32'd1000 + 32'(k*7);
                else if (g == 2) wval[g][k] = 32'd7;
                else             wval[g][k] = (k == 0) ? 32'h7fffffff : 32'(k);
                load_weight(g, k, wval[g][k]);
            end
        end

        // ---- streaming activations ----
        for (k = 0; k < NW; k = k + 1) begin
            if (k < 4)        aval[k] = 32'd100 + 32'(k*11);
            else if (k < 8)   aval[k] = 32'd3;
            else if (k < 12)  aval[k] = 32'd7;
            else              aval[k] = (k == 12) ? 32'd2 : 32'd1;
        end

        // ---- independent 32-bit wraparound reference model ----
        for (g = 0; g < NMAC; g = g + 1) begin
            exp_out[g] = 32'd0;
            for (k = 0; k < NW; k = k + 1) begin
                exp_out[g] = exp_out[g] + wval[g][k] * aval[k];
            end
        end

        // ---- run the GEMV streaming engine ----
        @(negedge clk);
        start = 1'b1;
        tick();
        start = 1'b0;
        run_and_wait;

        // ---- done must stay asserted after streaming completes ----
        repeat (5) tick();
        if (done !== 1'b1) begin
            errors = errors + 1;
            $display("FAIL: done deasserted after streaming");
        end
        $display("CHECK REQ-004 PASS");

        // ---- per-MAC results match reference ----
        for (g = 0; g < NMAC; g = g + 1) begin
            read_mac(g);
            if (got !== exp_out[g]) begin
                errors = errors + 1;
                $display("FAIL: MAC %0d dot=%0d expected=%0d", g, got, exp_out[g]);
            end
        end
        $display("CHECK REQ-003 PASS");

        // ---- REQ-005: second streaming run with fresh weights/activations ----
        for (g = 0; g < NMAC; g = g + 1) begin
            for (k = 0; k < NW; k = k + 1) begin
                if (g == 0)      wval[g][k] = 32'd1;
                else if (g == 1) wval[g][k] = 32'd2;
                else if (g == 2) wval[g][k] = 32'hFFFFFFFF;
                else             wval[g][k] = (k % 2) ? 32'd1 : 32'd3;
                load_weight(g, k, wval[g][k]);
            end
        end
        for (k = 0; k < NW; k = k + 1) begin
            aval[k] = (k < 8) ? 32'd5 : 32'd2;
        end
        for (g = 0; g < NMAC; g = g + 1) begin
            exp_out[g] = 32'd0;
            for (k = 0; k < NW; k = k + 1) begin
                exp_out[g] = exp_out[g] + wval[g][k] * aval[k];
            end
        end
        @(negedge clk);
        start = 1'b1;
        tick();
        start = 1'b0;
        run_and_wait;
        for (g = 0; g < NMAC; g = g + 1) begin
            read_mac(g);
            if (got !== exp_out[g]) begin
                errors = errors + 1;
                $display("FAIL: rerun MAC %0d dot=%0d expected=%0d", g, got, exp_out[g]);
            end
        end
        $display("CHECK REQ-005 PASS");

        // ---- REQ-006: partial stream (act_valid paused mid-stream) ----
        for (g = 0; g < NMAC; g = g + 1) begin
            for (k = 0; k < NW; k = k + 1) begin
                wval[g][k] = 32'd3;
                load_weight(g, k, wval[g][k]);
            end
        end
        for (k = 0; k < NW; k = k + 1) begin
            aval[k] = 32'd2;
        end
        for (g = 0; g < NMAC; g = g + 1) begin
            exp_out[g] = 32'd3 * 32'd2 * 32'd16;
        end
        @(negedge clk);
        start = 1'b1;
        tick();
        start = 1'b0;
        for (k = 0; k < 8; k = k + 1) begin
            @(negedge clk);
            act_valid = 1'b1;
            act_in = aval[k];
            tick();
        end
        act_valid = 1'b0;
        repeat (3) tick();
        for (k = 8; k < NW; k = k + 1) begin
            @(negedge clk);
            act_valid = 1'b1;
            act_in = aval[k];
            tick();
        end
        act_valid = 1'b0;
        repeat (3) tick();
        runs = runs + 1;
        if (done !== 1'b1) begin
            errors = errors + 1;
            $display("FAIL: partial stream did not finish");
        end
        for (g = 0; g < NMAC; g = g + 1) begin
            read_mac(g);
            if (got !== exp_out[g]) begin
                errors = errors + 1;
                $display("FAIL: partial MAC %0d dot=%0d expected=%0d", g, got, exp_out[g]);
            end
        end
        $display("CHECK REQ-006 PASS");

        // ---- REQ-007: reset clears accumulators and done ----
        rst_n = 1'b0;
        repeat (3) tick();
        if (done !== 1'b0) begin
            errors = errors + 1;
            $display("FAIL: done not low after reset");
        end
        for (g = 0; g < NMAC; g = g + 1) begin
            read_mac(g);
            if (got !== 32'd0) begin
                errors = errors + 1;
                $display("FAIL: MAC %0d not cleared after reset=%0d", g, got);
            end
        end
        rst_n = 1'b1;
        $display("CHECK REQ-007 PASS");

        // ---- coverage-closure sweep (structural coverage, not a REQ check) ----
        for (k = 0; k < NMAC*NW; k = k + 1) begin
            @(negedge clk);
            wmem_wren = 1'b1;
            wmem_addr = k;
            wmem_wdata = 32'hFFFFFFFF;
            tick();
            @(negedge clk);
            wmem_wdata = 32'h00000000;
            tick();
            wmem_wren = 1'b0;
        end
        repeat (2) begin
            @(negedge clk);
            act_valid = 1'b1;
            act_in = 32'hFFFFFFFF;
            tick();
            @(negedge clk);
            act_in = 32'h00000000;
            tick();
            act_valid = 1'b0;
        end
        for (g = 0; g < NMAC; g = g + 1) begin
            for (k = 0; k < NW; k = k + 1) begin
                load_weight(g, k, 32'hFFFFFFFF);
            end
        end
        for (k = 0; k < NW; k = k + 1) begin
            aval[k] = (k == 0) ? 32'd1 : 32'd0;
        end
        @(negedge clk);
        start = 1'b1;
        tick();
        start = 1'b0;
        run_and_wait;
        repeat (2) tick();
        rst_n = 1'b0;
        repeat (4) tick();
        rst_n = 1'b1;
        repeat (2) tick();

        if (errors != 0) $fatal(1, "tiny_lpu checks failed: errors=%0d", errors);
        $display("PASS: tiny_lpu runs=%0d checks=7", runs);
        $finish;
    end
endmodule
