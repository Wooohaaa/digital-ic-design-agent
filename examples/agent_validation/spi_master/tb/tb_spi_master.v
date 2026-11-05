	`timescale 1ns/1ps
module spi_case #(
    parameter integer TEST_CPOL = 0,
    parameter integer TEST_CPHA = 0
) (
    input wire clk,
    input wire rst_n,
    input wire launch,
    output reg finished
);
    localparam integer DIVIDER = 2;
    reg start = 0;
    reg [7:0] tx_data = 0;
    wire [7:0] rx_data;
    wire busy, done, sclk, mosi, cs_n;
    reg miso = 0;
    reg [7:0] expected_rx = 0;
    reg [7:0] tx_expected = 0;
    reg [7:0] tx_values [0:2];
    reg [7:0] rx_values [0:2];
    integer bit_index;
    integer transfer;

    spi_master #(.CLK_DIV(DIVIDER), .CPOL(TEST_CPOL), .CPHA(TEST_CPHA)) dut (
        .clk(clk), .rst_n(rst_n), .start(start), .tx_data(tx_data), .rx_data(rx_data),
        .busy(busy), .done(done), .sclk(sclk), .mosi(mosi), .miso(miso), .cs_n(cs_n)
    );

    initial begin
        tx_values[0] = 8'h96; rx_values[0] = 8'h3c;
        tx_values[1] = 8'h00; rx_values[1] = 8'ha5;
        tx_values[2] = 8'hff; rx_values[2] = 8'h81;
        finished = 0;
        wait (rst_n === 1'b1);
        if (sclk !== TEST_CPOL[0] || cs_n !== 1'b1 || busy !== 1'b0)
            $fatal(1, "SPI mode %0d%0d has invalid idle pins", TEST_CPOL, TEST_CPHA);
        wait (launch === 1'b1);
        for (transfer = 0; transfer < 3; transfer = transfer + 1) begin
            @(negedge clk);
            tx_data = tx_values[transfer];
            tx_expected = tx_values[transfer];
            expected_rx = rx_values[transfer];
            start = 1;
            @(negedge clk);
            start = 0;
            wait (done === 1'b1);
            #1;
            if (rx_data !== expected_rx)
                $fatal(1, "SPI mode %0d%0d RX mismatch: got %02x expected %02x",
                       TEST_CPOL, TEST_CPHA, rx_data, expected_rx);
            if (busy !== 1'b0 || cs_n !== 1'b1 || sclk !== TEST_CPOL[0])
                $fatal(1, "SPI mode %0d%0d did not return to idle", TEST_CPOL, TEST_CPHA);
            @(negedge clk);
        end
        finished = 1;
    end

    // Edge-driven target: CPHA=0 presents each input bit before its leading
    // sampling edge; CPHA=1 presents it at the leading edge before trailing sample.
    initial begin
        forever begin
            @(negedge cs_n);
            bit_index = 0;
            if (TEST_CPHA == 0) miso = expected_rx[7];
            else miso = 0;
            for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
                if (TEST_CPOL == 0) begin
                    @(posedge sclk);
                    if (TEST_CPHA == 1) miso = expected_rx[7-bit_index];
                    #1;
                    if (mosi !== tx_expected[7-bit_index])
                        $fatal(1, "SPI mode %0d%0d bit %0d TX mismatch", TEST_CPOL, TEST_CPHA, bit_index);
                    @(negedge sclk);
                    if (TEST_CPHA == 0 && bit_index < 7) miso = expected_rx[6-bit_index];
                end else begin
                    @(negedge sclk);
                    if (TEST_CPHA == 1) miso = expected_rx[7-bit_index];
                    #1;
                    if (mosi !== tx_expected[7-bit_index])
                        $fatal(1, "SPI mode %0d%0d bit %0d TX mismatch", TEST_CPOL, TEST_CPHA, bit_index);
                    @(posedge sclk);
                    if (TEST_CPHA == 0 && bit_index < 7) miso = expected_rx[6-bit_index];
                end
            end
        end
    end
endmodule

`timescale 1ns/1ps
module tb_spi_master;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst_n = 0;
    reg launch = 0;
    wire [3:0] finished;
    spi_case #(.TEST_CPOL(0), .TEST_CPHA(0)) c00(clk, rst_n, launch, finished[0]);
    spi_case #(.TEST_CPOL(0), .TEST_CPHA(1)) c01(clk, rst_n, launch, finished[1]);
    spi_case #(.TEST_CPOL(1), .TEST_CPHA(0)) c10(clk, rst_n, launch, finished[2]);
    spi_case #(.TEST_CPOL(1), .TEST_CPHA(1)) c11(clk, rst_n, launch, finished[3]);
    initial begin
        repeat (3) @(posedge clk);
        #1 rst_n = 1;
        @(negedge clk) launch = 1;
        @(negedge clk) launch = 0;
        wait (&finished);
        $display("CHECK REQ-001 PASS");
        $display("CHECK REQ-002 PASS");
        $display("CHECK REQ-003 PASS");
        $display("CHECK REQ-004 PASS");
        $display("PASS: spi_master four modes, twelve transfers");
        $finish;
    end
    initial begin
        #20000;
        $fatal(1, "SPI testbench timeout");
    end
endmodule
