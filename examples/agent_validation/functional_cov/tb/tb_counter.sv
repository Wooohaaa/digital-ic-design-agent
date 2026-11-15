`timescale 1ns/1ps
// Fixed testbench for the stage 5 functional-coverage fixture.
//
// The agent may edit only rtl/counter.sv; this bench is pinned by SHA-256 in
// project.json. It holds the counter in reset, then releases reset and runs
// `en` high long enough for the free-running counter to visit 0, 5 and 15 and
// wrap (hitting every `cover property`), then drops `en` to exercise the
// hold branch and pulses reset again to exercise the reset toggle.
module tb_counter;
    logic clk = 1'b0;
    logic rst_n = 1'b0;
    logic en = 1'b0;
    wire  [3:0] cnt;

    always #5 clk = ~clk;

    counter dut (.clk(clk), .rst_n(rst_n), .en(en), .cnt(cnt));

    integer errors = 0;
    reg [3:0] first;
    initial begin
        #1;
        repeat (4) @(posedge clk);
        if (cnt !== 4'd0) begin
            errors = errors + 1;
            $display("FAIL: reset cnt=%0d", cnt);
        end
        $display("CHECK reset PASS");

        rst_n = 1'b1;
        en = 1'b1;
        first = cnt;
        repeat (40) @(posedge clk);
        if (cnt === first) begin
            errors = errors + 1;
            $display("FAIL: cnt did not count");
        end
        $display("CHECK count PASS");

        // Exercise the en==0 hold branch.
        en = 1'b0;
        repeat (4) @(posedge clk);
        if (cnt !== 4'd8) begin
            errors = errors + 1;
            $display("FAIL: hold cnt=%0d", cnt);
        end
        $display("CHECK hold PASS");

        // Exercise the rst_n 1->0 toggle and re-initialisation.
        rst_n = 1'b0;
        repeat (4) @(posedge clk);
        if (cnt !== 4'd0) begin
            errors = errors + 1;
            $display("FAIL: final-reset cnt=%0d", cnt);
        end
        $display("CHECK final-reset PASS");
        rst_n = 1'b1;
        en = 1'b0;

        if (errors != 0) $fatal(1, "counter functional checks failed: errors=%0d", errors);
        $display("PASS: counter functional checks=4");
        $finish;
    end
endmodule
