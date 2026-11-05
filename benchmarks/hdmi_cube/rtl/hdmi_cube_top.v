module hdmi_cube_top #(
    // This board/receiver combination loses lock with a prolonged RGB=0
    // background. RGB=1 is visually black and was qualified on the capture
    // chain. Strict RGB=0 remains selectable for separately qualified sinks.
    parameter [7:0] BACKGROUND_LEVEL = 8'd1
) (
    input  wire       I_sys_clk,
    output wire [2:0] O_hdmi_data_p,
    output wire       O_hdmi_clk_p
);

    reg [15:0] por_counter = 16'd0;
    reg por_done = 1'b0;

    always @(posedge I_sys_clk) begin
        if (!por_done) begin
            if (por_counter == 16'hffff)
                por_done <= 1'b1;
            else
                por_counter <= por_counter + 1'b1;
        end
    end

    wire por_reset = !por_done;
    wire clk_pixel;
    wire clk_serial;
    wire pll_locked;

    hdmi_clocks u_hdmi_clocks (
        .clk50(I_sys_clk), .reset(por_reset), .clk_pixel(clk_pixel),
        .clk_serial(clk_serial), .locked(pll_locked)
    );

    wire pixel_reset_request = por_reset || !pll_locked;
    reg [1:0] pixel_reset_sync;

    always @(posedge clk_pixel or posedge pixel_reset_request) begin
        if (pixel_reset_request)
            pixel_reset_sync <= 2'b11;
        else
            pixel_reset_sync <= {pixel_reset_sync[0], 1'b0};
    end

    wire pixel_reset = pixel_reset_sync[1];
    wire [9:0] x;
    wire [9:0] y;
    wire timing_de;
    wire timing_hsync;
    wire timing_vsync;
    wire frame_start;

    video_timing u_video_timing (
        .clk(clk_pixel), .reset(pixel_reset), .x(x), .y(y),
        .de(timing_de), .hsync(timing_hsync), .vsync(timing_vsync),
        .frame_start(frame_start)
    );

    wire [7:0] angle;
    wire [79:0] vertices_x;
    wire [79:0] vertices_y;
    wire pixel_on;
    wire framebuffer_busy;

    cube_vertices u_cube_vertices (
        .angle(angle), .vertices_x(vertices_x), .vertices_y(vertices_y)
    );

    cube_framebuffer u_cube_framebuffer (
        .clk(clk_pixel), .reset(pixel_reset), .frame_start(frame_start),
        .x(x), .y(y), .vertices_x(vertices_x), .vertices_y(vertices_y),
        .angle(angle), .pixel_on(pixel_on), .busy(framebuffer_busy)
    );

    reg de_delayed;
    reg hsync_delayed;
    reg vsync_delayed;

    always @(posedge clk_pixel) begin
        if (pixel_reset) begin
            de_delayed <= 1'b0;
            hsync_delayed <= 1'b1;
            vsync_delayed <= 1'b1;
        end else begin
            de_delayed <= timing_de;
            hsync_delayed <= timing_hsync;
            vsync_delayed <= timing_vsync;
        end
    end

    wire [7:0] red_data = pixel_on ? 8'd40 : BACKGROUND_LEVEL;
    wire [7:0] green_data = pixel_on ? 8'd220 : BACKGROUND_LEVEL;
    wire [7:0] blue_data = pixel_on ? 8'd255 : BACKGROUND_LEVEL;
    wire [9:0] blue_symbol;
    wire [9:0] green_symbol;
    wire [9:0] red_symbol;

    tmds_encoder u_blue_encoder (
        .clk(clk_pixel), .reset(pixel_reset), .data(blue_data),
        .de(de_delayed), .c0(hsync_delayed), .c1(vsync_delayed),
        .symbol(blue_symbol)
    );

    tmds_encoder u_green_encoder (
        .clk(clk_pixel), .reset(pixel_reset), .data(green_data),
        .de(de_delayed), .c0(1'b0), .c1(1'b0), .symbol(green_symbol)
    );

    tmds_encoder u_red_encoder (
        .clk(clk_pixel), .reset(pixel_reset), .data(red_data),
        .de(de_delayed), .c0(1'b0), .c1(1'b0), .symbol(red_symbol)
    );

    hdmi_serializer u_hdmi_serializer (
        .clk_pixel(clk_pixel), .clk_serial(clk_serial), .reset(pixel_reset),
        .blue(blue_symbol), .green(green_symbol), .red(red_symbol),
        .data_p(O_hdmi_data_p), .clock_p(O_hdmi_clk_p)
    );

endmodule
