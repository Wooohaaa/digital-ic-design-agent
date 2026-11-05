module sync_fifo #(
    parameter integer DATA_WIDTH = 8,
    parameter integer DEPTH = 4
) (
    input  wire                  clk,
    input  wire                  rst_n,
    input  wire                  wr_en,
    input  wire                  rd_en,
    input  wire [DATA_WIDTH-1:0] din,
    output wire [DATA_WIDTH-1:0] dout,
    output wire                  full,
    output wire                  empty
);
    // TODO: Implement the FIFO to the fixed contract in spec/design.md.
    assign dout = {DATA_WIDTH{1'b0}};
    assign full = 1'b0;
    assign empty = 1'b1;
endmodule
