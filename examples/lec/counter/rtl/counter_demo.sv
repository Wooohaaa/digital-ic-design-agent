module counter_demo #(
    parameter RESET_ACTIVE = 1'b1
) (
    input wire clk, rst, en,
    output wire [7:0] q
);
    wire reset_high = rst == RESET_ACTIVE;
    counter core(.clk(clk), .rst(reset_high), .en(en), .q(q));
endmodule
