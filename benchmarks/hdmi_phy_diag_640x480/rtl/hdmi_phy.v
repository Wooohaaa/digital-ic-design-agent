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
        .drp_clk(clk50),
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
        .psclk(clk50),
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
    PH1P_LOGIC_HR_OSERDES #(
        .ODDRMODE("DDRX5")
    ) u_hdmi_oserdes (
        .sclk(clk_serial),
        .pclk(clk_pixel),
        .rst(reset),
        .idata(symbol),
        .odata(serial_data)
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
    output wire [2:0] data_n,
    output wire       clock_p,
    output wire       clock_n
);

    wire [2:0] serial_data;

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

`ifdef ICFORGE_SIM
    assign data_p = serial_data;
    assign data_n = ~serial_data;
    assign clock_p = clk_pixel;
    assign clock_n = ~clk_pixel;
`else
    genvar lane;
    generate
        for (lane = 0; lane < 3; lane = lane + 1) begin : g_hdmi_diff_data
            PH1P_LOGIC_OBUF_DIFF u_hdmi_data_diff (
                .i(serial_data[lane]),
                .t(1'b0),
                .o_p(data_p[lane]),
                .o_n(data_n[lane])
            );
        end
    endgenerate

    PH1P_LOGIC_OBUF_DIFF u_hdmi_clock_diff (
        .i(clk_pixel),
        .t(1'b0),
        .o_p(clock_p),
        .o_n(clock_n)
    );
`endif

endmodule
