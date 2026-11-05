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
    localparam integer DIV_WIDTH = (CLK_DIV <= 2) ? 1 : $clog2(CLK_DIV);
    localparam [DIV_WIDTH-1:0] DIV_TERMINAL_COUNT = CLK_DIV[DIV_WIDTH-1:0] - 1'b1;

    reg [DIV_WIDTH-1:0] div_count;
    reg [2:0] bit_count;
    reg [6:0] tx_shift;
    reg [7:0] rx_shift;
    reg [7:0] rx_data_reg;
    reg busy_reg;
    reg done_reg;
    reg sclk_reg;
    reg mosi_reg;
    reg cs_n_reg;

    assign rx_data = rx_data_reg;
    assign busy = busy_reg;
    assign done = done_reg;
    assign sclk = sclk_reg;
    assign mosi = mosi_reg;
    assign cs_n = cs_n_reg;

    always @(posedge clk) begin
        if (!rst_n) begin
            div_count <= 0;
            bit_count <= 0;
            tx_shift <= 0;
            rx_shift <= 0;
            rx_data_reg <= 0;
            busy_reg <= 0;
            done_reg <= 0;
            sclk_reg <= CPOL[0];
            mosi_reg <= 0;
            cs_n_reg <= 1;
        end else begin
            done_reg <= 0;

            if (!busy_reg) begin
                if (start) begin
                    div_count <= 0;
                    bit_count <= 0;
                    tx_shift <= tx_data[6:0];
                    rx_shift <= 0;
                    mosi_reg <= tx_data[7];
                    sclk_reg <= CPOL[0];
                    cs_n_reg <= 0;
                    busy_reg <= 1;
                end
            end else if (div_count == DIV_TERMINAL_COUNT) begin
                div_count <= 0;

                if (sclk_reg == CPOL[0]) begin
                    sclk_reg <= ~CPOL[0];
                    if (CPHA[0] && bit_count != 0) begin
                        mosi_reg <= tx_shift[6];
                        tx_shift <= {tx_shift[5:0], 1'b0};
                    end
                    if (!CPHA[0])
                        rx_shift <= {rx_shift[6:0], miso};
                end else begin
                    sclk_reg <= CPOL[0];
                    if (CPHA[0]) begin
                        rx_shift <= {rx_shift[6:0], miso};
                        if (bit_count == 3'd7) begin
                            rx_data_reg <= {rx_shift[6:0], miso};
                            busy_reg <= 0;
                            cs_n_reg <= 1;
                            done_reg <= 1;
                        end else begin
                            bit_count <= bit_count + 1'b1;
                        end
                    end else if (bit_count == 3'd7) begin
                        rx_data_reg <= rx_shift;
                        busy_reg <= 0;
                        cs_n_reg <= 1;
                        done_reg <= 1;
                    end else begin
                        bit_count <= bit_count + 1'b1;
                        mosi_reg <= tx_shift[6];
                        tx_shift <= {tx_shift[5:0], 1'b0};
                    end
                end
            end else begin
                div_count <= div_count + 1'b1;
            end
        end
    end
endmodule
