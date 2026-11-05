module cube_framebuffer(
    input clk,
    input reset,
    input frame_start,
    input [9:0] x,
    input [9:0] y,
    input [79:0] vertices_x,
    input [79:0] vertices_y,
    output reg [7:0] angle,
    output reg pixel_on,
    output busy
);

localparam [2:0] ST_CAPTURE = 3'd0;
localparam [2:0] ST_CLEAR = 3'd1;
localparam [2:0] ST_EDGE_SETUP = 3'd2;
localparam [2:0] ST_DRAW = 3'd3;
localparam [2:0] ST_READY = 3'd4;

reg [2:0] state;
reg front_bank;
reg back_bank;
reg front_valid;
reg [18:0] clear_address;
reg [3:0] edge_index;
reg [9:0] vertex_x [0:7];
reg [9:0] vertex_y [0:7];
reg [2:0] line_start_index;
reg [2:0] line_end_index;
reg [9:0] line_x;
reg [9:0] line_y;
reg [9:0] line_target_x;
reg [9:0] line_target_y;
reg [9:0] line_dx;
reg signed [11:0] line_dy;
reg signed [11:0] line_error;
reg step_x_positive;
reg step_y_positive;
reg read_data0;
reg read_data1;
reg read_bank_q;
reg read_valid_q;

reg framebuffer0 [0:307199];
reg framebuffer1 [0:307199];

integer capture_index;

wire input_address_valid = (x < 10'd640) && (y < 10'd480);
wire [19:0] scan_row_offset = ({10'b0, y} << 9) + ({10'b0, y} << 7);
wire [19:0] scan_address_full = scan_row_offset + {10'b0, x};
wire [18:0] scan_address = input_address_valid ? scan_address_full[18:0] : 19'd0;
wire [19:0] draw_row_offset = ({10'b0, line_y} << 9) + ({10'b0, line_y} << 7);
wire [19:0] draw_address_full = draw_row_offset + {10'b0, line_x};
wire [18:0] draw_address = draw_address_full[18:0];

wire [9:0] edge_start_x = vertex_x[line_start_index];
wire [9:0] edge_start_y = vertex_y[line_start_index];
wire [9:0] edge_end_x = vertex_x[line_end_index];
wire [9:0] edge_end_y = vertex_y[line_end_index];
wire [9:0] edge_abs_dx = (edge_end_x >= edge_start_x) ?
                         (edge_end_x - edge_start_x) : (edge_start_x - edge_end_x);
wire [9:0] edge_abs_dy = (edge_end_y >= edge_start_y) ?
                         (edge_end_y - edge_start_y) : (edge_start_y - edge_end_y);
wire signed [11:0] line_dx_signed = $signed({2'b00, line_dx});
wire signed [12:0] doubled_error = $signed({line_error[11], line_error}) <<< 1;
wire signed [12:0] line_dy_extended = {{1{line_dy[11]}}, line_dy};
wire signed [12:0] line_dx_extended = {3'b000, line_dx};
wire take_x_step = doubled_error >= line_dy_extended;
wire take_y_step = doubled_error <= line_dx_extended;

always @* begin
    line_start_index = 3'd0;
    line_end_index = 3'd1;
    case (edge_index)
        4'd0: begin line_start_index = 3'd0; line_end_index = 3'd1; end
        4'd1: begin line_start_index = 3'd2; line_end_index = 3'd3; end
        4'd2: begin line_start_index = 3'd4; line_end_index = 3'd5; end
        4'd3: begin line_start_index = 3'd6; line_end_index = 3'd7; end
        4'd4: begin line_start_index = 3'd0; line_end_index = 3'd2; end
        4'd5: begin line_start_index = 3'd1; line_end_index = 3'd3; end
        4'd6: begin line_start_index = 3'd4; line_end_index = 3'd6; end
        4'd7: begin line_start_index = 3'd5; line_end_index = 3'd7; end
        4'd8: begin line_start_index = 3'd0; line_end_index = 3'd4; end
        4'd9: begin line_start_index = 3'd1; line_end_index = 3'd5; end
        4'd10: begin line_start_index = 3'd2; line_end_index = 3'd6; end
        4'd11: begin line_start_index = 3'd3; line_end_index = 3'd7; end
        default: begin line_start_index = 3'd0; line_end_index = 3'd1; end
    endcase
end

assign busy = (state != ST_READY);

always @* begin
    pixel_on = 1'b0;
    if (read_valid_q) begin
        if (read_bank_q)
            pixel_on = read_data1;
        else
            pixel_on = read_data0;
    end
end

always @(posedge clk) begin
    if (front_bank == 1'b0)
        read_data0 <= framebuffer0[scan_address];
    else
        read_data1 <= framebuffer1[scan_address];
end

always @(posedge clk) begin
    if (!reset) begin
        if ((state == ST_CLEAR) && (back_bank == 1'b0))
            framebuffer0[clear_address] <= 1'b0;
        else if ((state == ST_DRAW) && (back_bank == 1'b0))
            framebuffer0[draw_address] <= 1'b1;
    end
end

always @(posedge clk) begin
    if (!reset) begin
        if ((state == ST_CLEAR) && (back_bank == 1'b1))
            framebuffer1[clear_address] <= 1'b0;
        else if ((state == ST_DRAW) && (back_bank == 1'b1))
            framebuffer1[draw_address] <= 1'b1;
    end
end

always @(posedge clk) begin
    if (reset) begin
        state <= ST_CAPTURE;
        front_bank <= 1'b0;
        back_bank <= 1'b1;
        front_valid <= 1'b0;
        clear_address <= 19'd0;
        edge_index <= 4'd0;
        line_x <= 10'd0;
        line_y <= 10'd0;
        line_target_x <= 10'd0;
        line_target_y <= 10'd0;
        line_dx <= 10'd0;
        line_dy <= 12'sd0;
        line_error <= 12'sd0;
        step_x_positive <= 1'b1;
        step_y_positive <= 1'b1;
        angle <= 8'd0;
        read_bank_q <= 1'b0;
        read_valid_q <= 1'b0;
    end else begin
        read_bank_q <= front_bank;
        read_valid_q <= front_valid && input_address_valid;

        case (state)
            ST_CAPTURE: begin
                for (capture_index = 0; capture_index < 8; capture_index = capture_index + 1) begin
                    vertex_x[capture_index] <= vertices_x[capture_index * 10 +: 10];
                    vertex_y[capture_index] <= vertices_y[capture_index * 10 +: 10];
                end
                clear_address <= 19'd0;
                state <= ST_CLEAR;
            end

            ST_CLEAR: begin
                if (clear_address == 19'd307199) begin
                    edge_index <= 4'd0;
                    state <= ST_EDGE_SETUP;
                end else begin
                    clear_address <= clear_address + 1'b1;
                end
            end

            ST_EDGE_SETUP: begin
                line_x <= edge_start_x;
                line_y <= edge_start_y;
                line_target_x <= edge_end_x;
                line_target_y <= edge_end_y;
                line_dx <= edge_abs_dx;
                line_dy <= -$signed({2'b00, edge_abs_dy});
                line_error <= $signed({2'b00, edge_abs_dx}) - $signed({2'b00, edge_abs_dy});
                step_x_positive <= (edge_end_x >= edge_start_x);
                step_y_positive <= (edge_end_y >= edge_start_y);
                state <= ST_DRAW;
            end

            ST_DRAW: begin
                if ((line_x == line_target_x) && (line_y == line_target_y)) begin
                    if (edge_index == 4'd11)
                        state <= ST_READY;
                    else begin
                        edge_index <= edge_index + 1'b1;
                        state <= ST_EDGE_SETUP;
                    end
                end else begin
                    if (take_x_step) begin
                        if (step_x_positive)
                            line_x <= line_x + 1'b1;
                        else
                            line_x <= line_x - 1'b1;
                    end
                    if (take_y_step) begin
                        if (step_y_positive)
                            line_y <= line_y + 1'b1;
                        else
                            line_y <= line_y - 1'b1;
                    end
                    if (take_x_step && take_y_step)
                        line_error <= line_error + line_dy + line_dx_signed;
                    else if (take_x_step)
                        line_error <= line_error + line_dy;
                    else if (take_y_step)
                        line_error <= line_error + line_dx_signed;
                end
            end

            ST_READY: begin
                if (frame_start) begin
                    front_bank <= back_bank;
                    back_bank <= front_bank;
                    front_valid <= 1'b1;
                    angle <= angle + 1'b1;
                    state <= ST_CAPTURE;
                end
            end

            default: begin
                state <= ST_CAPTURE;
            end
        endcase
    end
end

endmodule
