module color_bars (
    input  wire [9:0] x,
    input  wire       de,
    output reg  [7:0] red,
    output reg  [7:0] green,
    output reg  [7:0] blue
);
    always @* begin
        red = 8'd0;
        green = 8'd0;
        blue = 8'd0;
        if (de) begin
            if      (x < 10'd80)  begin red=8'hff; green=8'hff; blue=8'hff; end
            else if (x < 10'd160) begin red=8'hff; green=8'hff; blue=8'h00; end
            else if (x < 10'd240) begin red=8'h00; green=8'hff; blue=8'hff; end
            else if (x < 10'd320) begin red=8'h00; green=8'hff; blue=8'h00; end
            else if (x < 10'd400) begin red=8'hff; green=8'h00; blue=8'hff; end
            else if (x < 10'd480) begin red=8'hff; green=8'h00; blue=8'h00; end
            else if (x < 10'd560) begin red=8'h00; green=8'h00; blue=8'hff; end
        end
    end
endmodule
