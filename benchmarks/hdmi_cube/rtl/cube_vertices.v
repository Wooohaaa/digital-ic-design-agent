module cube_vertices(
    input  [7:0]  angle,
    output [79:0] vertices_x,
    output [79:0] vertices_y
);

    function signed [9:0] quarter_sine_q8;
        input [6:0] index;
        begin
            case (index)
                7'd0:  quarter_sine_q8 = 10'sd0;
                7'd1:  quarter_sine_q8 = 10'sd6;
                7'd2:  quarter_sine_q8 = 10'sd13;
                7'd3:  quarter_sine_q8 = 10'sd19;
                7'd4:  quarter_sine_q8 = 10'sd25;
                7'd5:  quarter_sine_q8 = 10'sd31;
                7'd6:  quarter_sine_q8 = 10'sd38;
                7'd7:  quarter_sine_q8 = 10'sd44;
                7'd8:  quarter_sine_q8 = 10'sd50;
                7'd9:  quarter_sine_q8 = 10'sd56;
                7'd10: quarter_sine_q8 = 10'sd62;
                7'd11: quarter_sine_q8 = 10'sd68;
                7'd12: quarter_sine_q8 = 10'sd74;
                7'd13: quarter_sine_q8 = 10'sd80;
                7'd14: quarter_sine_q8 = 10'sd86;
                7'd15: quarter_sine_q8 = 10'sd92;
                7'd16: quarter_sine_q8 = 10'sd98;
                7'd17: quarter_sine_q8 = 10'sd104;
                7'd18: quarter_sine_q8 = 10'sd109;
                7'd19: quarter_sine_q8 = 10'sd115;
                7'd20: quarter_sine_q8 = 10'sd121;
                7'd21: quarter_sine_q8 = 10'sd126;
                7'd22: quarter_sine_q8 = 10'sd132;
                7'd23: quarter_sine_q8 = 10'sd137;
                7'd24: quarter_sine_q8 = 10'sd142;
                7'd25: quarter_sine_q8 = 10'sd147;
                7'd26: quarter_sine_q8 = 10'sd152;
                7'd27: quarter_sine_q8 = 10'sd157;
                7'd28: quarter_sine_q8 = 10'sd162;
                7'd29: quarter_sine_q8 = 10'sd167;
                7'd30: quarter_sine_q8 = 10'sd172;
                7'd31: quarter_sine_q8 = 10'sd177;
                7'd32: quarter_sine_q8 = 10'sd181;
                7'd33: quarter_sine_q8 = 10'sd185;
                7'd34: quarter_sine_q8 = 10'sd190;
                7'd35: quarter_sine_q8 = 10'sd194;
                7'd36: quarter_sine_q8 = 10'sd198;
                7'd37: quarter_sine_q8 = 10'sd202;
                7'd38: quarter_sine_q8 = 10'sd206;
                7'd39: quarter_sine_q8 = 10'sd209;
                7'd40: quarter_sine_q8 = 10'sd213;
                7'd41: quarter_sine_q8 = 10'sd216;
                7'd42: quarter_sine_q8 = 10'sd220;
                7'd43: quarter_sine_q8 = 10'sd223;
                7'd44: quarter_sine_q8 = 10'sd226;
                7'd45: quarter_sine_q8 = 10'sd229;
                7'd46: quarter_sine_q8 = 10'sd231;
                7'd47: quarter_sine_q8 = 10'sd234;
                7'd48: quarter_sine_q8 = 10'sd237;
                7'd49: quarter_sine_q8 = 10'sd239;
                7'd50: quarter_sine_q8 = 10'sd241;
                7'd51: quarter_sine_q8 = 10'sd243;
                7'd52: quarter_sine_q8 = 10'sd245;
                7'd53: quarter_sine_q8 = 10'sd247;
                7'd54: quarter_sine_q8 = 10'sd248;
                7'd55: quarter_sine_q8 = 10'sd250;
                7'd56: quarter_sine_q8 = 10'sd251;
                7'd57: quarter_sine_q8 = 10'sd252;
                7'd58: quarter_sine_q8 = 10'sd253;
                7'd59: quarter_sine_q8 = 10'sd254;
                7'd60: quarter_sine_q8 = 10'sd255;
                7'd61: quarter_sine_q8 = 10'sd255;
                7'd62: quarter_sine_q8 = 10'sd256;
                7'd63: quarter_sine_q8 = 10'sd256;
                7'd64: quarter_sine_q8 = 10'sd256;
                default: quarter_sine_q8 = 10'sd0;
            endcase
        end
    endfunction

    function signed [9:0] sine_q8;
        input [7:0] theta;
        reg [6:0] quarter_index;
        reg signed [9:0] magnitude;
        reg negative;
        begin
            case (theta[7:6])
                2'b00: begin
                    quarter_index = {1'b0, theta[5:0]};
                    negative = 1'b0;
                end
                2'b01: begin
                    quarter_index = 7'd64 - {1'b0, theta[5:0]};
                    negative = 1'b0;
                end
                2'b10: begin
                    quarter_index = {1'b0, theta[5:0]};
                    negative = 1'b1;
                end
                default: begin
                    quarter_index = 7'd64 - {1'b0, theta[5:0]};
                    negative = 1'b1;
                end
            endcase
            magnitude = quarter_sine_q8(quarter_index);
            if (negative)
                sine_q8 = -magnitude;
            else
                sine_q8 = magnitude;
        end
    endfunction

    function signed [9:0] cosine_q8;
        input [7:0] theta;
        begin
            cosine_q8 = sine_q8(theta + 8'd64);
        end
    endfunction

    wire [9:0] second_angle_sum;
    wire [7:0] angle_b;
    wire signed [9:0] sin_a;
    wire signed [9:0] cos_a;
    wire signed [9:0] sin_b;
    wire signed [9:0] cos_b;

    assign second_angle_sum = {1'b0, angle, 1'b0} + 10'd32;
    assign angle_b = second_angle_sum[7:0];
    assign sin_a = sine_q8(angle);
    assign cos_a = cosine_q8(angle);
    assign sin_b = sine_q8(angle_b);
    assign cos_b = cosine_q8(angle_b);

    genvar vertex;
    generate
        for (vertex = 0; vertex < 8; vertex = vertex + 1) begin : vertex_geometry
            wire signed [7:0] initial_x;
            wire signed [7:0] initial_y;
            wire signed [7:0] initial_z;

            wire signed [17:0] x_extended;
            wire signed [17:0] y_extended;
            wire signed [17:0] z_extended;
            wire signed [17:0] sin_a_extended;
            wire signed [17:0] cos_a_extended;
            wire signed [17:0] sin_b_extended;
            wire signed [17:0] cos_b_extended;

            wire signed [17:0] x_cos_a;
            wire signed [17:0] z_sin_a;
            wire signed [17:0] x_sin_a;
            wire signed [17:0] z_cos_a;
            wire signed [18:0] x_cos_a_extended;
            wire signed [18:0] z_sin_a_extended;
            wire signed [18:0] x_sin_a_extended;
            wire signed [18:0] z_cos_a_extended;
            wire signed [18:0] xr_sum;
            wire signed [18:0] zr_sum;
            wire signed [18:0] xr_shifted;
            wire signed [18:0] zr_shifted;
            wire signed [7:0] xr;
            wire signed [7:0] zr;

            wire signed [17:0] y_cos_b;
            wire signed [17:0] zr_sin_b;
            wire signed [18:0] y_cos_b_extended;
            wire signed [18:0] zr_sin_b_extended;
            wire signed [18:0] yr_difference;
            wire signed [18:0] yr_shifted;
            wire signed [7:0] yr;

            wire signed [9:0] xr_extended;
            wire signed [9:0] yr_extended;
            wire signed [9:0] xr_times_three;
            wire signed [9:0] yr_times_three;
            wire signed [9:0] xr_scaled;
            wire signed [9:0] yr_scaled;
            wire signed [10:0] screen_x;
            wire signed [10:0] screen_y;

            assign initial_x = ((vertex & 1) != 0) ? 8'sd64 : -8'sd64;
            assign initial_y = ((vertex & 2) != 0) ? 8'sd64 : -8'sd64;
            assign initial_z = ((vertex & 4) != 0) ? 8'sd64 : -8'sd64;

            assign x_extended = {{10{initial_x[7]}}, initial_x};
            assign y_extended = {{10{initial_y[7]}}, initial_y};
            assign z_extended = {{10{initial_z[7]}}, initial_z};
            assign sin_a_extended = {{8{sin_a[9]}}, sin_a};
            assign cos_a_extended = {{8{cos_a[9]}}, cos_a};
            assign sin_b_extended = {{8{sin_b[9]}}, sin_b};
            assign cos_b_extended = {{8{cos_b[9]}}, cos_b};

            assign x_cos_a = x_extended * cos_a_extended;
            assign z_sin_a = z_extended * sin_a_extended;
            assign x_sin_a = x_extended * sin_a_extended;
            assign z_cos_a = z_extended * cos_a_extended;
            assign x_cos_a_extended = {x_cos_a[17], x_cos_a};
            assign z_sin_a_extended = {z_sin_a[17], z_sin_a};
            assign x_sin_a_extended = {x_sin_a[17], x_sin_a};
            assign z_cos_a_extended = {z_cos_a[17], z_cos_a};
            assign xr_sum = x_cos_a_extended + z_sin_a_extended;
            assign zr_sum = -x_sin_a_extended + z_cos_a_extended;
            assign xr_shifted = xr_sum >>> 8;
            assign zr_shifted = zr_sum >>> 8;
            assign xr = xr_shifted[7:0];
            assign zr = zr_shifted[7:0];

            wire signed [17:0] zr_extended;
            assign zr_extended = {{10{zr[7]}}, zr};
            assign y_cos_b = y_extended * cos_b_extended;
            assign zr_sin_b = zr_extended * sin_b_extended;
            assign y_cos_b_extended = {y_cos_b[17], y_cos_b};
            assign zr_sin_b_extended = {zr_sin_b[17], zr_sin_b};
            assign yr_difference = y_cos_b_extended - zr_sin_b_extended;
            assign yr_shifted = yr_difference >>> 8;
            assign yr = yr_shifted[7:0];

            assign xr_extended = {{2{xr[7]}}, xr};
            assign yr_extended = {{2{yr[7]}}, yr};
            assign xr_times_three = xr_extended + (xr_extended <<< 1);
            assign yr_times_three = yr_extended + (yr_extended <<< 1);
            assign xr_scaled = xr_times_three >>> 1;
            assign yr_scaled = yr_times_three >>> 1;
            assign screen_x = 11'sd320 + {{1{xr_scaled[9]}}, xr_scaled};
            assign screen_y = 11'sd240 - {{1{yr_scaled[9]}}, yr_scaled};

            assign vertices_x[vertex * 10 +: 10] = screen_x[9:0];
            assign vertices_y[vertex * 10 +: 10] = screen_y[9:0];
        end
    endgenerate

endmodule
