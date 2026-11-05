`ifdef ICFORGE_SIM
`timescale 1ps/1ps
`endif

module hdmi_clocks (
    input  wire clk50,
    input  wire reset,
    output wire clk_pixel,
    output wire clk_serial,
    output wire locked
);

`ifdef ICFORGE_SIM
    reg sim_clk_pixel;
    reg sim_clk_serial;
    reg sim_locked;
    reg [1:0] sim_lock_count;

    initial begin
        sim_clk_pixel = 1'b0;
        forever #20000 sim_clk_pixel = ~sim_clk_pixel;
    end

    initial begin
        sim_clk_serial = 1'b0;
        forever #4000 sim_clk_serial = ~sim_clk_serial;
    end

    initial begin
        sim_locked = 1'b0;
        sim_lock_count = 2'd0;
    end

    always @(posedge sim_clk_pixel or posedge reset) begin
        if (reset) begin
            sim_locked <= 1'b0;
            sim_lock_count <= 2'd0;
        end else if (!sim_locked) begin
            if (sim_lock_count == 2'd3) begin
                sim_locked <= 1'b1;
            end else begin
                sim_lock_count <= sim_lock_count + 1'b1;
            end
        end
    end

    assign clk_pixel = sim_clk_pixel;
    assign clk_serial = sim_clk_serial;
    assign locked = sim_locked;
`else
    wire [6:0] pll_clk;
    wire [6:0] pll_clk_b;
    wire pll_locked;
    wire pll_feedback_clk;

    // Match the supplied PH1P35 reference design's external-feedback topology.
    PH1P_LOGIC_BUFG u_pll_feedback_bufg (
        .i(pll_clk[0]),
        .o(pll_feedback_clk)
    );

    PH1P_PHY_PLL_V2 #(
        .FBKCLK("CLKC0_EXT"),
        .FBKCLK_INT("CLKC0_EXT"),
        .FIN("50.0000"),
        .REFCLK_DIV(1),
        .FBCLK_DIV(1),
        .CLKC0_DIV(25),
        .CLKC4_DIV(50),
        .CLKC5_DIV(10),
        .CLKC0_CPHASE(24),
        .CLKC4_CPHASE(49),
        .CLKC5_CPHASE(9),
        .CLKC0_DUTY_INT(13),
        .CLKC4_DUTY_INT(25),
        .CLKC5_DUTY_INT(5),
        .CLKC0_DUTY50("ENABLE"),
        .CLKC4_DUTY50("ENABLE"),
        .CLKC5_DUTY50("ENABLE"),
        .CLKC0_ENABLE("ENABLE"),
        .CLKC4_ENABLE("ENABLE"),
        .CLKC5_ENABLE("ENABLE"),
        .PLL_USR_RST("ENABLE"),
        .PLL_FEED_TYPE("EXTERNAL"),
        .PLL_FASTLOOP("ENABLE"),
        .LPF_RES(0),
        .LPF_CAP(5),
        .ICP_CUR(29),
        .SSC_ENABLE("DISABLE")
    ) u_hdmi_pll (
        .refclk(clk50),
        .refclk_rst(1'b0),
        .fbclk(pll_feedback_clk),
        .clkc(pll_clk),
        .clkcb(pll_clk_b),
        .lock(pll_locked),
        .pllreset(reset),
        .clkc_rst(2'b00),
        .clkc_en(7'b0110001),
        .wakeup(1'b1),
        .cps_step(1'b0),
        .pllpd(1'b0),
        // DRP and dynamic phase shifting are disabled. Match the generated
        // Anlogic reference: do not route the system clock into inactive ports.
        .drp_clk(1'b0),
        .drp_rstn(1'b1),
        .drp_sel(1'b0),
        .drp_rd(1'b0),
        .drp_wr(1'b0),
        .drp_addr(8'b0),
        .drp_wdata(8'b0),
        .drp_err(),
        .drp_rdy(),
        .drp_rdata(),
        .psdone(),
        .psclk(1'b0),
        .psdown(1'b0),
        .psstep(1'b0),
        .psclksel(3'b0),
        .ssc_reset(1'b0),
        .scan_clk(1'b1),
        .scan_enable(1'b1),
        .scan_mode(1'b0),
        .scan_resetn(1'b1)
    );

    assign clk_pixel = pll_clk[4];
    assign clk_serial = pll_clk[5];
    assign locked = pll_locked;
`endif

endmodule

module hdmi_serial_lane (
    input  wire       clk_pixel,
    input  wire       clk_serial,
    input  wire       reset,
    input  wire [9:0] symbol,
    output wire       serial_data
);

`ifdef ICFORGE_SIM
    reg [9:0] sim_symbol;
    reg [3:0] sim_bit_index;
    reg sim_serial_data;

    initial begin
        sim_symbol = 10'b0;
        sim_bit_index = 4'd0;
        sim_serial_data = 1'b0;
    end

    always @(posedge clk_pixel or posedge reset) begin
        if (reset) begin
            sim_symbol <= 10'b0;
            sim_bit_index <= 4'd0;
        end else begin
            sim_symbol <= symbol;
            sim_bit_index <= 4'd0;
        end
    end

    always @(posedge clk_serial or negedge clk_serial or posedge reset) begin
        if (reset) begin
            sim_serial_data <= 1'b0;
        end else begin
            #1;
            sim_serial_data <= sim_symbol[sim_bit_index];
            if (sim_bit_index == 4'd9) begin
                sim_bit_index <= 4'd0;
            end else begin
                sim_bit_index <= sim_bit_index + 1'b1;
            end
        end
    end

    // Simulation alignment: each pixel edge captures one symbol; bit 0 launches on the coincident serial edge, then bits advance on every DDR edge. The one-picosecond delta avoids an event-order race at that shared edge.
    assign serial_data = sim_serial_data;
`else
    reg odd_even;
    reg odd_even_1d;
    reg odd_even_2d;
    reg load_en;
    reg load_en_1d;
    reg load_en_2d;
    reg [9:0] shift_data;
    reg [1:0] oddr_data;
    wire [9:0] reversed_symbol = {
        symbol[0], symbol[1], symbol[2], symbol[3], symbol[4],
        symbol[5], symbol[6], symbol[7], symbol[8], symbol[9]
    };

    // Same 10:1 DDR launch structure used by the supplied Anlogic PH1P example.
    always @(posedge clk_pixel or posedge reset) begin
        if (reset)
            odd_even <= 1'b0;
        else
            odd_even <= ~odd_even;
    end

    always @(posedge clk_serial) begin
        odd_even_1d <= odd_even;
        odd_even_2d <= odd_even_1d;
        load_en <= odd_even_1d ^ odd_even_2d;
        load_en_1d <= load_en;
        load_en_2d <= load_en_1d;
    end

    always @(posedge clk_serial or posedge reset) begin
        if (reset)
            shift_data <= 10'd0;
        else if (load_en_2d)
            shift_data <= reversed_symbol;
        else
            shift_data <= shift_data << 2;
    end

    always @(posedge clk_serial)
        oddr_data <= shift_data[9:8];

    PH1P_LOGIC_ODDR u_hdmi_oddr (
        .clk(clk_serial),
        .rst(reset),
        .d1(oddr_data[0]),
        .d0(oddr_data[1]),
        .q(serial_data)
    );
`endif

endmodule

module hdmi_serializer (
    input  wire       clk_pixel,
    input  wire       clk_serial,
    input  wire       reset,
    input  wire [9:0] blue,
    input  wire [9:0] green,
    input  wire [9:0] red,
    output wire [2:0] data_p,
    output wire       clock_p
);

    wire [2:0] serial_data;
    wire serial_clock;

    hdmi_serial_lane u_hdmi_lane_blue (
        .clk_pixel(clk_pixel),
        .clk_serial(clk_serial),
        .reset(reset),
        .symbol(blue),
        .serial_data(serial_data[0])
    );

    hdmi_serial_lane u_hdmi_lane_green (
        .clk_pixel(clk_pixel),
        .clk_serial(clk_serial),
        .reset(reset),
        .symbol(green),
        .serial_data(serial_data[1])
    );

    hdmi_serial_lane u_hdmi_lane_red (
        .clk_pixel(clk_pixel),
        .clk_serial(clk_serial),
        .reset(reset),
        .symbol(red),
        .serial_data(serial_data[2])
    );

    // The board reference serializes the clock channel through the same
    // 5x DDR output lane. LSB-first 0000011111 yields a 50% pixel clock.
    hdmi_serial_lane u_hdmi_lane_clock (
        .clk_pixel(clk_pixel),
        .clk_serial(clk_serial),
        .reset(reset),
        .symbol(10'b0000011111),
        .serial_data(serial_clock)
    );

`ifdef ICFORGE_SIM
    assign data_p = serial_data;
    assign clock_p = serial_clock;
`else
    // HX1P35A's reference design routes the ODDR output straight to the
    // TMDS33 P-side top-level pin. TD infers the paired N pad from the P pin.
    assign data_p = serial_data;
    assign clock_p = serial_clock;
`endif

endmodule
