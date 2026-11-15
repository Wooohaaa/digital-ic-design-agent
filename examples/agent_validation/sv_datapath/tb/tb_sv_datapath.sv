`timescale 1ns/1ps
module tb_sv_datapath;
    localparam integer WIDTH = 8;
    localparam integer DEPTH = 4;
    logic clk = 1'b0;
    always #5 clk = ~clk;
    logic rst_n = 1'b0;
    logic load = 1'b0;
    logic start = 1'b0;
    logic shift_en = 1'b0;
    logic [WIDTH-1:0] din = '0;
    wire [WIDTH-1:0] sum;
    wire [WIDTH-1:0] shift_out;
    wire busy;

    sv_datapath #(.DATA_WIDTH(WIDTH), .DEPTH(DEPTH)) dut (
        .clk(clk), .rst_n(rst_n), .load(load), .start(start), .shift_en(shift_en),
        .din(din), .sum(sum), .shift_out(shift_out), .busy(busy)
    );

    integer errors = 0;
    integer runs = 0;
    logic [WIDTH-1:0] ref_shift [0:DEPTH-1];
    integer s;
    logic [WIDTH-1:0] expected_sum;

    task automatic tick; begin
        @(posedge clk); #1;
    end endtask

    task automatic load_beat(input logic [WIDTH-1:0] value); begin
        @(negedge clk);
        load = 1'b1; din = value;
        tick();
        load = 1'b0;
    end endtask

    task automatic run_and_check(input logic [WIDTH-1:0] value);
        begin
            // Return to idle before loading: the RTL only latches a beat in IDLE.
            while (busy === 1'b1) tick();
            tick();
            load_beat(value);
            expected_sum = value * DEPTH;
            @(negedge clk);
            start = 1'b1;
            tick();
            start = 1'b0;
            // busy must assert on the first RUN edge.
            tick();
            if (busy !== 1'b1) begin
                errors = errors + 1;
                $display("FAIL: busy not asserted during RUN");
            end
            while (busy === 1'b1) tick();
            if (sum !== expected_sum) begin
                errors = errors + 1;
                $display("FAIL: sum=%02x expected=%02x", sum, expected_sum);
            end
            runs = runs + 1;
        end
    endtask

    integer i;
    logic [WIDTH-1:0] seed;
    initial begin
        repeat (3) tick();
        if (sum !== '0 || busy !== 1'b0) $fatal(1, "reset state incorrect");
        rst_n = 1'b1;
        tick();

        // REQ-004: shift register delays din by DEPTH stages.
        for (s = 0; s < DEPTH; s = s + 1) ref_shift[s] = '0;
        for (i = 0; i < 12; i = i + 1) begin
            @(negedge clk);
            shift_en = 1'b1;
            din = WIDTH'(i + 8'h40);
            tick();
            for (s = DEPTH-1; s > 0; s = s - 1) ref_shift[s] = ref_shift[s-1];
            ref_shift[0] = din;
            if (shift_out !== ref_shift[DEPTH-1]) begin
                errors = errors + 1;
                $display("FAIL: shift_out=%02x expected=%02x", shift_out, ref_shift[DEPTH-1]);
            end
        end
        @(negedge clk);
        shift_en = 1'b0;

        // REQ-002 / REQ-003 / REQ-005: directed and deterministic random runs.
        run_and_check(8'h03);
        run_and_check(8'h01);
        run_and_check(8'h00);
        seed = 8'hA5;
        for (i = 0; i < 40; i = i + 1) begin
            seed = {seed[6:0], seed[7] ^ seed[5] ^ seed[4] ^ seed[3]};
            run_and_check(seed);
        end

        if (errors != 0) $fatal(1, "sv_datapath checks failed: errors=%0d", errors);
        if (runs < 40) $fatal(1, "too few runs exercised: %0d", runs);
        $display("CHECK REQ-001 PASS");
        $display("CHECK REQ-002 PASS");
        $display("CHECK REQ-003 PASS");
        $display("CHECK REQ-004 PASS");
        $display("CHECK REQ-005 PASS");
        $display("PASS: sv_datapath runs=%0d", runs);
        $finish;
    end
endmodule
