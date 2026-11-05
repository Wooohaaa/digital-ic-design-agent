`timescale 1ps/1ps

module tb_hdmi_phy;
    reg clk50 = 1'b0;
    reg reset = 1'b1;
    wire clk_pixel;
    wire clk_serial;
    wire locked;
    reg [9:0] blue = 10'b1011001001;
    reg [9:0] green = 10'b0110101100;
    reg [9:0] red = 10'b1100010110;
    reg [9:0] blue_patterns [0:3];
    reg [9:0] green_patterns [0:3];
    reg [9:0] red_patterns [0:3];
    reg drive_enable = 1'b0;
    integer drive_index = 0;
    wire [2:0] data_p;
    wire [2:0] data_n;
    wire clock_p;
    wire clock_n;
    time last_pixel_edge;
    time last_serial_edge;
    integer pixel_edges = 0;
    integer serial_edges = 0;
    integer capture_index;
    integer offset;
    integer bit_index;
    integer matched;
    integer word_match;
    integer pattern_index;
    integer word_index;
    reg [2:0] captured [0:59];

    always @(negedge clk_pixel) begin
        if (drive_enable) begin
            case (drive_index % 4)
                0: begin blue = blue_patterns[0]; green = green_patterns[0]; red = red_patterns[0]; end
                1: begin blue = blue_patterns[1]; green = green_patterns[1]; red = red_patterns[1]; end
                2: begin blue = blue_patterns[2]; green = green_patterns[2]; red = red_patterns[2]; end
                3: begin blue = blue_patterns[3]; green = green_patterns[3]; red = red_patterns[3]; end
            endcase
            drive_index = drive_index + 1;
        end
    end

    always #10000 clk50 = ~clk50;

    hdmi_clocks clocks (
        .clk50(clk50), .reset(reset), .clk_pixel(clk_pixel),
        .clk_serial(clk_serial), .locked(locked)
    );

    hdmi_serializer serializer (
        .clk_pixel(clk_pixel), .clk_serial(clk_serial), .reset(reset),
        .blue(blue), .green(green), .red(red), .data_p(data_p),
        .data_n(data_n), .clock_p(clock_p), .clock_n(clock_n)
    );

    always @(posedge clk_pixel) begin
        if (!reset) begin
            if (pixel_edges > 0 && ($time - last_pixel_edge) != 39680)
                $fatal(1, "behavioral pixel clock period is not 39680 ps");
            last_pixel_edge = $time;
            pixel_edges = pixel_edges + 1;
        end
    end

    always @(posedge clk_serial) begin
        if (!reset) begin
            if (serial_edges > 0 && ($time - last_serial_edge) != 7936)
                $fatal(1, "behavioral serial clock period is not 7936 ps");
            last_serial_edge = $time;
            serial_edges = serial_edges + 1;
        end
    end

    task check_lane_words;
        input [9:0] expected_blue;
        input [9:0] expected_green;
        input [9:0] expected_red;
        begin
            for (capture_index = 0; capture_index < 10; capture_index = capture_index + 1) begin
                @(posedge clk_serial);
                #1;
                captured[capture_index * 2] = data_p;
                @(negedge clk_serial);
                #1;
                captured[capture_index * 2 + 1] = data_p;
            end

            matched = 0;
            for (offset = 0; offset < 10; offset = offset + 1) begin
                word_match = 1;
                for (bit_index = 0; bit_index < 10; bit_index = bit_index + 1) begin
                    if ((captured[offset + bit_index][0] !== expected_blue[bit_index]) ||
                        (captured[offset + bit_index][1] !== expected_green[bit_index]) ||
                        (captured[offset + bit_index][2] !== expected_red[bit_index]))
                        word_match = 0;
                end
                if (word_match)
                    matched = matched + 1;
            end
            if (matched == 0)
                $fatal(1, "serialized lanes did not match bit0-first symbols");
        end
    endtask

    task check_lane_sequence;
        begin
            for (capture_index = 0; capture_index < 30; capture_index = capture_index + 1) begin
                @(posedge clk_serial);
                #1;
                captured[capture_index * 2] = data_p;
                @(negedge clk_serial);
                #1;
                captured[capture_index * 2 + 1] = data_p;
            end

            matched = 0;
            for (offset = 0; offset < 10; offset = offset + 1) begin
                for (pattern_index = 0; pattern_index < 4; pattern_index = pattern_index + 1) begin
                    word_match = 1;
                    for (word_index = 0; word_index < 5; word_index = word_index + 1) begin
                        for (bit_index = 0; bit_index < 10; bit_index = bit_index + 1) begin
                            if ((captured[offset + word_index * 10 + bit_index][0] !==
                                 blue_patterns[(pattern_index + word_index) % 4][bit_index]) ||
                                (captured[offset + word_index * 10 + bit_index][1] !==
                                 green_patterns[(pattern_index + word_index) % 4][bit_index]) ||
                                (captured[offset + word_index * 10 + bit_index][2] !==
                                 red_patterns[(pattern_index + word_index) % 4][bit_index]))
                                word_match = 0;
                        end
                    end
                    if (word_match)
                        matched = matched + 1;
                end
            end
            if (matched == 0)
                $fatal(1, "serial stream lost symbol boundaries or lane ordering");
        end
    endtask

    initial begin
        blue_patterns[0] = 10'b1011001001;
        blue_patterns[1] = 10'b0110101110;
        blue_patterns[2] = 10'b1100010101;
        blue_patterns[3] = 10'b0001111010;
        green_patterns[0] = 10'b0110101100;
        green_patterns[1] = 10'b1001010011;
        green_patterns[2] = 10'b0011100110;
        green_patterns[3] = 10'b1110001001;
        red_patterns[0] = 10'b1100010110;
        red_patterns[1] = 10'b0011011001;
        red_patterns[2] = 10'b1010110001;
        red_patterns[3] = 10'b0101101110;

        repeat (4) @(posedge clk50);
        @(negedge clk50);
        reset = 1'b0;

        capture_index = 0;
        while ((locked !== 1'b1) && (capture_index < 200)) begin
            @(posedge clk50);
            capture_index = capture_index + 1;
        end
        if (locked !== 1'b1)
            $fatal(1, "simulation PLL model did not assert locked");

        repeat (6) @(posedge clk_pixel);
        check_lane_words(blue, green, red);
        @(posedge clk_pixel);
        drive_enable = 1'b1;
        check_lane_sequence();

        if (pixel_edges < 10 || serial_edges < 50)
            $fatal(1, "clock monitors did not observe enough generated edges");
        if ((data_p ^ data_n) !== 3'b111 || (clock_p ^ clock_n) !== 1'b1)
            $fatal(1, "differential simulation outputs are not complementary");

        $display("PASS tb_hdmi_phy");
        $finish;
    end
endmodule
