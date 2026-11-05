module spi_master #(
    parameter integer CLK_DIV = 2,
    parameter integer CPOL = 0,
    parameter integer CPHA = 0
) (
    input wire clk,
    input wire rst_n,
    input wire start,
    input wire [7:0] tx_data,
    output wire [7:0] rx_data,
    output wire busy,
    output wire done,
    output wire sclk,
    output wire mosi,
    input wire miso,
    output wire cs_n
);
    // TODO: Implement all four modes to the fixed contract in spec/design.md.
    assign rx_data = 8'h00;
    assign busy = 1'b0;
    assign done = 1'b0;
    assign sclk = CPOL[0];
    assign mosi = 1'b0;
    assign cs_n = 1'b1;
endmodule
