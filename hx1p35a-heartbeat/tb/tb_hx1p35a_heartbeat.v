`timescale 1ns/1ps

module tb_hx1p35a_heartbeat;
    localparam [31:0] HALF_PERIOD_CYCLES = 32'd4;

    reg I_sys_clk = 1'b0;
    reg I_rst_n = 1'b0;
    wire O_screen_pwm;
    integer cycle_index;

    always #10 I_sys_clk = ~I_sys_clk;

    hx1p35a_heartbeat #(
        .HALF_PERIOD_CYCLES(HALF_PERIOD_CYCLES)
    ) dut (
        .I_sys_clk(I_sys_clk),
        .I_rst_n(I_rst_n),
        .O_screen_pwm(O_screen_pwm)
    );

    initial begin
        $dumpfile("build/heartbeat.vcd");
        $dumpvars(0, tb_hx1p35a_heartbeat);

        repeat (2) @(posedge I_sys_clk);
        #1;
        if (O_screen_pwm !== 1'b0) $fatal(1, "FAIL: reset must clear output");

        @(negedge I_sys_clk);
        I_rst_n = 1'b1;

        for (cycle_index = 1; cycle_index <= 8; cycle_index = cycle_index + 1) begin
            @(posedge I_sys_clk);
            #1;
            if (cycle_index < 4 && O_screen_pwm !== 1'b0)
                $fatal(1, "FAIL: output changed before terminal count at cycle %0d", cycle_index);
            if (cycle_index == 4 && O_screen_pwm !== 1'b1)
                $fatal(1, "FAIL: output did not set at first terminal count");
            if (cycle_index > 4 && cycle_index < 8 && O_screen_pwm !== 1'b1)
                $fatal(1, "FAIL: output did not remain stable between terminal counts");
            if (cycle_index == 8 && O_screen_pwm !== 1'b0)
                $fatal(1, "FAIL: output did not toggle at second terminal count");
        end

        $display("PASS: reset, divide count, hold, and output toggle verified");
        $finish;
    end
endmodule
