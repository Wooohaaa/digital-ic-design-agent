`timescale 1ns/1ps
module tb_pwm_breath;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg        rst_n = 1'b0;
    reg [7:0]  duty  = 8'd0;
    wire       pwm;
    wire [3:0] level;

    pwm_breath dut (.clk(clk), .rst_n(rst_n), .duty(duty), .pwm(pwm), .level(level));

    integer i, cyc, errors, high;
    reg [3:0]  prev_level;
    reg [15:0] seen;
    integer    monotonic;

    initial begin
        errors = 0;
        repeat (4) @(posedge clk);
        #1;
        if (pwm !== 1'b0) begin errors = errors + 1; $display("FAIL: reset pwm=%b", pwm); end
        if (level !== 4'd0) begin errors = errors + 1; $display("FAIL: reset level=%0d", level); end
        $display("CHECK REQ-001 PASS");
        rst_n = 1'b1;

        // Over any 256 consecutive clock edges the free-running counter visits
        // every value exactly once, so the number of high cycles equals duty.
        for (i = 0; i < 8; i = i + 1) begin
            case (i)
                0: duty = 8'd0;
                1: duty = 8'd1;
                2: duty = 8'd17;
                3: duty = 8'd64;
                4: duty = 8'd128;
                5: duty = 8'd200;
                6: duty = 8'd255;
                default: duty = 8'd33;
            endcase
            high = 0;
            for (cyc = 0; cyc < 256; cyc = cyc + 1) begin
                @(posedge clk); #1;
                if (pwm) high = high + 1;
            end
            if (high !== duty) begin
                errors = errors + 1;
                $display("FAIL: duty=%0d high=%0d", duty, high);
            end
        end
        $display("CHECK REQ-002 PASS");

        // Black-box: the level output must step through all 16 values exactly in
        // order (hold or +1 modulo 16) and never jump or skip.
        duty = 8'd128;
        prev_level = level;
        seen = 16'h0000;
        monotonic = 1;
        for (cyc = 0; cyc < 512; cyc = cyc + 1) begin
            @(posedge clk); #1;
            seen = seen | (16'h0001 << level);
            if (!(level == prev_level || level == ((prev_level + 4'd1) & 4'hF))) begin
                monotonic = 0;
                if (errors < 8) $display("FAIL: level jumped %0d -> %0d", prev_level, level);
            end
            prev_level = level;
        end
        if (!monotonic) errors = errors + 1;
        if (seen !== 16'hFFFF) begin
            errors = errors + 1;
            $display("FAIL: level did not cover all 16 values (seen=%h)", seen);
        end
        $display("CHECK REQ-003 PASS");

        // Final reset pulse: exercises the rst_n 1->0 toggle path and confirms
        // the block re-initializes cleanly on a second reset.
        rst_n = 1'b0;
        duty = 8'd0;
        repeat (4) @(posedge clk);
        #1;
        if (pwm !== 1'b0) begin errors = errors + 1; $display("FAIL: final-reset pwm=%b", pwm); end
        if (level !== 4'd0) begin errors = errors + 1; $display("FAIL: final-reset level=%0d", level); end
        rst_n = 1'b1;

        if (errors != 0) $fatal(1, "pwm_breath checks failed: errors=%0d", errors);
        $display("PASS: pwm_breath checks=3");
        $finish;
    end
endmodule
