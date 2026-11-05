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
    localparam integer PTR_WIDTH = (DEPTH <= 2) ? 1 : $clog2(DEPTH);
    localparam integer COUNT_WIDTH = $clog2(DEPTH + 1);

    reg [DATA_WIDTH-1:0] storage [0:DEPTH-1];
    reg [PTR_WIDTH-1:0] write_pointer;
    reg [PTR_WIDTH-1:0] read_pointer;
    reg [COUNT_WIDTH-1:0] occupancy;
    reg [DATA_WIDTH-1:0] data_out;

    wire [31:0] write_pointer_extended;
    wire [31:0] read_pointer_extended;
    wire [31:0] occupancy_extended;

    wire write_accepted = wr_en && !full;
    wire read_accepted = rd_en && !empty;

    assign write_pointer_extended = {{(32-PTR_WIDTH){1'b0}}, write_pointer};
    assign read_pointer_extended = {{(32-PTR_WIDTH){1'b0}}, read_pointer};
    assign occupancy_extended = {{(32-COUNT_WIDTH){1'b0}}, occupancy};

    assign dout = data_out;
    assign full = (occupancy_extended == DEPTH);
    assign empty = (occupancy_extended == 32'd0);

    always @(posedge clk) begin
        if (!rst_n) begin
            write_pointer <= 0;
            read_pointer <= 0;
            occupancy <= 0;
            data_out <= 0;
        end else begin
            if (write_accepted) begin
                storage[write_pointer] <= din;
                if (write_pointer_extended == (DEPTH - 1))
                    write_pointer <= 0;
                else
                    write_pointer <= write_pointer + 1'b1;
            end

            if (read_accepted) begin
                data_out <= storage[read_pointer];
                if (read_pointer_extended == (DEPTH - 1))
                    read_pointer <= 0;
                else
                    read_pointer <= read_pointer + 1'b1;
            end

            case ({write_accepted, read_accepted})
                2'b10: occupancy <= occupancy + 1'b1;
                2'b01: occupancy <= occupancy - 1'b1;
                default: occupancy <= occupancy;
            endcase
        end
    end
endmodule
