`timescale 1ns/1ps

module tb_video_timing;
    reg clk = 1'b0;
    reg reset = 1'b1;
    wire [9:0] x;
    wire [9:0] y;
    wire de;
    wire hsync;
    wire vsync;
    wire frame_start;
    integer expected_x;
    integer expected_y;
    integer frame_starts;
    integer cycle;

    always #10 clk = ~clk;

    video_timing dut (
        .clk(clk), .reset(reset), .x(x), .y(y), .de(de),
        .hsync(hsync), .vsync(vsync), .frame_start(frame_start)
    );

    initial begin
        repeat (2) @(posedge clk);
        #1;
        if (x !== 10'd0 || y !== 10'd0 || de !== 1'b1 ||
            hsync !== 1'b1 || vsync !== 1'b1 || frame_start !== 1'b0)
            $fatal(1, "timing reset state mismatch");

        @(negedge clk);
        reset = 1'b0;
        expected_x = 1;
        expected_y = 0;
        frame_starts = 0;

        for (cycle = 0; cycle < 840000; cycle = cycle + 1) begin
            @(posedge clk);
            #1;
            if (x !== expected_x[9:0] || y !== expected_y[9:0])
                $fatal(1, "counter mismatch at cycle %0d: got %0d,%0d expected %0d,%0d",
                       cycle, x, y, expected_x, expected_y);
            if (de !== ((expected_x < 640) && (expected_y < 480)))
                $fatal(1, "DE mismatch at %0d,%0d", expected_x, expected_y);
            if (hsync !== !((expected_x >= 656) && (expected_x < 752)))
                $fatal(1, "HSYNC mismatch at x=%0d", expected_x);
            if (vsync !== !((expected_y >= 490) && (expected_y < 492)))
                $fatal(1, "VSYNC mismatch at y=%0d", expected_y);
            if (frame_start !== ((expected_x == 0) && (expected_y == 480)))
                $fatal(1, "frame_start mismatch at %0d,%0d", expected_x, expected_y);
            if (frame_start)
                frame_starts = frame_starts + 1;

            if (expected_x == 799) begin
                expected_x = 0;
                if (expected_y == 524)
                    expected_y = 0;
                else
                    expected_y = expected_y + 1;
            end else begin
                expected_x = expected_x + 1;
            end
        end

        if (frame_starts != 2)
            $fatal(1, "expected two frame_start pulses, got %0d", frame_starts);
        $display("PASS tb_video_timing");
        $finish;
    end
endmodule
