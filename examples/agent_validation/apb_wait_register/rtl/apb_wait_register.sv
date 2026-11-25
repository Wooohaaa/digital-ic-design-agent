module apb_wait_register (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       psel,
    input  wire       penable,
    input  wire       pwrite,
    input  wire [7:0] paddr,
    input  wire [7:0] pwdata,
    output wire [7:0] prdata,
    output wire       pready,
    output wire       pslverr
);
    localparam [1:0] IDLE = 2'd0, WAIT_ACCESS = 2'd1, COMPLETE = 2'd2;
    localparam [7:0] DATA_ADDR = 8'h00, COUNT_ADDR = 8'h04;

    reg [1:0] phase;
    reg setup_write;
    reg [7:0] setup_addr, setup_data;
    reg [7:0] value, write_count;

    wire setup = psel && !penable;
    wire access = psel && penable;
    wire data_selected = setup_addr == DATA_ADDR;
    wire count_selected = setup_addr == COUNT_ADDR;
    wire valid = data_selected || (count_selected && !setup_write);

    assign pready = rst_n && access && (phase == COMPLETE);
    assign pslverr = pready && !valid;
    assign prdata = (pready && valid && !setup_write) ?
                    (data_selected ? value : write_count) : 8'h00;

    always @(posedge clk) begin
        if (!rst_n) begin
            phase <= IDLE;
            setup_write <= 1'b0;
            setup_addr <= 8'h00;
            setup_data <= 8'h00;
            value <= 8'h00;
            write_count <= 8'h00;
        end else begin
            if (setup) begin
                setup_write <= pwrite;
                setup_addr <= paddr;
                setup_data <= pwdata;
                phase <= WAIT_ACCESS;
            end else if (access && phase == WAIT_ACCESS) begin
                phase <= COMPLETE;
            end else begin
                phase <= IDLE;
            end

            if (pready && setup_write && valid) begin
                value <= setup_data;
                write_count <= write_count + 8'd1;
            end
        end
    end
endmodule
