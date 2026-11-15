`timescale 1ns/1ps
module tb_hdmi_diag;
    reg clk = 1'b0;
    reg reset = 1'b1;
    always #20 clk = ~clk;

    wire [9:0] x, y;
    wire de, hsync, vsync, frame_start;
    video_timing timing(clk, reset, x, y, de, hsync, vsync, frame_start);

    reg [9:0] color_x;
    reg color_de;
    wire [7:0] red, green, blue;
    color_bars bars(color_x, color_de, red, green, blue);

    reg sys_clk = 1'b0;
    always #10 sys_clk = ~sys_clk;
    wire [2:0] data_p, data_n;
    wire clock_p, clock_n, hpd;
    hdmi_cube_top dut(sys_clk, data_p, data_n, clock_p, clock_n, hpd);

    integer i, ex, ey;
    reg [23:0] expected_color;
    initial begin
        color_de = 1'b1;
        for (i = 0; i < 8; i = i + 1) begin
            color_x = i * 80 + 10;
            #1;
            case (i)
                0: expected_color = 24'hffffff;
                1: expected_color = 24'hffff00;
                2: expected_color = 24'h00ffff;
                3: expected_color = 24'h00ff00;
                4: expected_color = 24'hff00ff;
                5: expected_color = 24'hff0000;
                6: expected_color = 24'h0000ff;
                default: expected_color = 24'h000000;
            endcase
            if ({red,green,blue} !== expected_color)
                $fatal(1, "bar %0d color mismatch: %h", i, {red,green,blue});
        end
        color_de = 1'b0;
        color_x = 10'd0;
        #1;
        if ({red,green,blue} !== 24'h000000)
            $fatal(1, "blanked color is not black");
        $display("CHECK REQ-001 PASS");

        reset = 1'b1;
        repeat (2) @(posedge clk);
        #1;
        reset = 1'b0;
        for (i = 0; i < 420000; i = i + 1) begin
            ex = i % 800;
            ey = (i / 800) % 525;
            #1;
            if (x !== ex || y !== ey || de !== ((ex < 640) && (ey < 480)) ||
                hsync !== !((ex >= 656) && (ex < 752)) ||
                vsync !== !((ey >= 490) && (ey < 492)) ||
                frame_start !== ((ex == 0) && (ey == 480)))
                $fatal(1, "raster mismatch at %0d,%0d", x, y);
            @(posedge clk);
            #1;
        end
        $display("CHECK REQ-002 PASS");

        wait (hpd === 1'b1);
        repeat (100) begin
            @(posedge clock_p);
            #1;
            if ((^data_p) === 1'bx || data_n !== ~data_p || clock_n !== ~clock_p)
                $fatal(1, "TMDS differential pair invalid");
        end
        $display("CHECK REQ-003 PASS");
        $display("PASS: 640x480 static HDMI PHY diagnostic");
        $finish;
    end
    initial begin
        #30000000;
        $fatal(1, "diagnostic simulation timeout");
    end
endmodule
