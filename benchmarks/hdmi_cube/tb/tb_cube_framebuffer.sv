`timescale 1ns/1ps

module tb_cube_framebuffer;
    reg clk = 1'b0;
    reg reset = 1'b1;
    reg frame_start = 1'b0;
    reg [9:0] x = 10'd0;
    reg [9:0] y = 10'd0;
    wire [79:0] vertices_x;
    wire [79:0] vertices_y;
    wire [7:0] angle;
    wire pixel_on;
    wire busy;
    integer cycles;

    always #5 clk = ~clk;

    cube_vertices geometry (
        .angle(angle), .vertices_x(vertices_x), .vertices_y(vertices_y)
    );

    cube_framebuffer dut (
        .clk(clk), .reset(reset), .frame_start(frame_start), .x(x), .y(y),
        .vertices_x(vertices_x), .vertices_y(vertices_y), .angle(angle),
        .pixel_on(pixel_on), .busy(busy)
    );

    task check_pixel;
        input integer pixel_x;
        input integer pixel_y;
        input expected_on;
        begin
            @(negedge clk);
            x = pixel_x[9:0];
            y = pixel_y[9:0];
            @(posedge clk);
            #1;
            if (pixel_on !== expected_on)
                $fatal(1, "pixel mismatch at (%0d,%0d): got %b expected %b",
                       pixel_x, pixel_y, pixel_on, expected_on);
        end
    endtask

    initial begin
        repeat (2) @(posedge clk);
        @(negedge clk);
        reset = 1'b0;

        cycles = 0;
        while ((busy !== 1'b1) && (cycles < 10)) begin
            @(posedge clk);
            #1;
            cycles = cycles + 1;
        end
        if (busy !== 1'b1)
            $fatal(1, "renderer did not enter busy state");
        check_pixel(224, 105, 1'b0);

        cycles = 0;
        while ((busy !== 1'b0) && (cycles < 420000)) begin
            @(posedge clk);
            #1;
            cycles = cycles + 1;
        end
        if (busy !== 1'b0)
            $fatal(1, "first frame did not finish within 420000 pixel clocks");
        if (angle !== 8'd0)
            $fatal(1, "first rendered frame must use angle zero");

        @(negedge clk);
        frame_start = 1'b1;
        @(posedge clk);
        #1;
        if (angle !== 8'd1)
            $fatal(1, "angle did not advance after completed-frame swap");
        @(negedge clk);
        frame_start = 1'b0;

        check_pixel(224, 105, 1'b1);
        check_pixel(320, 105, 1'b1);
        check_pixel(416, 105, 1'b1);
        check_pixel(224, 172, 1'b1);
        check_pixel(416, 240, 1'b1);
        check_pixel(224, 308, 1'b1);
        check_pixel(224, 377, 1'b1);
        check_pixel(320, 377, 1'b1);
        check_pixel(416, 377, 1'b1);
        check_pixel(320, 200, 1'b0);
        check_pixel(0, 0, 1'b0);
        check_pixel(640, 0, 1'b0);
        check_pixel(0, 480, 1'b0);
        check_pixel(1023, 1023, 1'b0);

        $display("PASS tb_cube_framebuffer");
        $finish;
    end
endmodule
