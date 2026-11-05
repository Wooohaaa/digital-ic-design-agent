`timescale 1ns/1ps

module tb_cube_vertices;
    reg [7:0] angle = 8'd0;
    wire [79:0] vertices_x;
    wire [79:0] vertices_y;
    real radians_a;
    real radians_b;
    integer sin_a;
    integer cos_a;
    integer sin_b;
    integer cos_b;
    integer angle_index;
    integer vertex_index;
    integer x_value;
    integer y_value;
    integer z_value;
    integer x_rotated;
    integer z_rotated;
    integer y_rotated;
    integer expected_x;
    integer expected_y;

    cube_vertices dut (
        .angle(angle), .vertices_x(vertices_x), .vertices_y(vertices_y)
    );

    function integer round_fixed;
        input real value;
        begin
            if (value >= 0.0)
                round_fixed = $rtoi(value + 0.5);
            else
                round_fixed = $rtoi(value - 0.5);
        end
    endfunction

    initial begin
        for (angle_index = 0; angle_index < 256; angle_index = angle_index + 1) begin
            angle = angle_index[7:0];
            #1;
            radians_a = 6.2831853071795864769 * angle_index / 256.0;
            radians_b = 6.2831853071795864769 * ((2 * angle_index + 32) % 256) / 256.0;
            sin_a = round_fixed(256.0 * $sin(radians_a));
            cos_a = round_fixed(256.0 * $cos(radians_a));
            sin_b = round_fixed(256.0 * $sin(radians_b));
            cos_b = round_fixed(256.0 * $cos(radians_b));

            for (vertex_index = 0; vertex_index < 8; vertex_index = vertex_index + 1) begin
                x_value = (vertex_index & 1) ? 64 : -64;
                y_value = (vertex_index & 2) ? 64 : -64;
                z_value = (vertex_index & 4) ? 64 : -64;
                x_rotated = (x_value * cos_a + z_value * sin_a) >>> 8;
                z_rotated = (-x_value * sin_a + z_value * cos_a) >>> 8;
                y_rotated = (y_value * cos_b - z_rotated * sin_b) >>> 8;
                expected_x = 320 + ((x_rotated * 3) >>> 1);
                expected_y = 240 - ((y_rotated * 3) >>> 1);

                if (vertices_x[vertex_index * 10 +: 10] !== expected_x[9:0])
                    $fatal(1, "X mismatch angle=%0d vertex=%0d got=%0d expected=%0d",
                           angle_index, vertex_index,
                           vertices_x[vertex_index * 10 +: 10], expected_x);
                if (vertices_y[vertex_index * 10 +: 10] !== expected_y[9:0])
                    $fatal(1, "Y mismatch angle=%0d vertex=%0d got=%0d expected=%0d",
                           angle_index, vertex_index,
                           vertices_y[vertex_index * 10 +: 10], expected_y);
                if ((expected_x < 0) || (expected_x >= 640) ||
                    (expected_y < 0) || (expected_y >= 480))
                    $fatal(1, "reference vertex outside active frame");
            end
        end

        $display("PASS tb_cube_vertices");
        $finish;
    end
endmodule
