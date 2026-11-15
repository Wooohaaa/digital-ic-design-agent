module video_timing(
    input clk,
    input reset,
    output reg [9:0] x,
    output reg [9:0] y,
    output de,
    output hsync,
    output vsync,
    output frame_start
);

    always @(posedge clk) begin
        if (reset) begin
            x <= 10'd0;
            y <= 10'd0;
        end else if (x == 10'd799) begin
            x <= 10'd0;
            if (y == 10'd524)
                y <= 10'd0;
            else
                y <= y + 10'd1;
        end else begin
            x <= x + 10'd1;
        end
    end

    assign de = (x < 10'd640) && (y < 10'd480);
    assign hsync = !((x >= 10'd656) && (x < 10'd752));
    assign vsync = !((y >= 10'd490) && (y < 10'd492));
    assign frame_start = (x == 10'd0) && (y == 10'd480);

endmodule
