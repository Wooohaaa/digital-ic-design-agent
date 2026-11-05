`timescale 1ns/1ps
module tb_sync_fifo;
    localparam integer WIDTH = 8;
    localparam integer DEPTH = 4;
    reg clk = 1'b0;
    always #5 clk = ~clk;
    reg rst_n = 1'b0;
    reg wr_en = 1'b0;
    reg rd_en = 1'b0;
    reg [WIDTH-1:0] din = 0;
    wire [WIDTH-1:0] dout;
    wire full, empty;
    sync_fifo #(.DATA_WIDTH(WIDTH), .DEPTH(DEPTH)) dut (
        .clk(clk), .rst_n(rst_n), .wr_en(wr_en), .rd_en(rd_en),
        .din(din), .dout(dout), .full(full), .empty(empty)
    );

    reg [WIDTH-1:0] model [0:DEPTH-1];
    integer head = 0;
    integer tail = 0;
    integer count = 0;
    integer operations = 0;
    reg saw_simultaneous = 0;
    reg [15:0] lfsr = 16'h1d0f;

    task automatic cycle(input reg do_write, input reg do_read, input reg [WIDTH-1:0] value);
        reg accept_write;
        reg accept_read;
        reg [WIDTH-1:0] expected;
        begin
            @(negedge clk);
            wr_en = do_write;
            rd_en = do_read;
            din = value;
            accept_write = do_write && (count < DEPTH);
            accept_read = do_read && (count > 0);
            if (accept_read) expected = model[head];
            @(posedge clk);
            #1;
            if (accept_read && dout !== expected)
                $fatal(1, "FIFO order/data error: got %02x expected %02x", dout, expected);
            if (accept_read) begin
                head = (head + 1) % DEPTH;
                count = count - 1;
            end
            if (accept_write) begin
                model[tail] = value;
                tail = (tail + 1) % DEPTH;
                count = count + 1;
            end
            if (do_write && do_read) saw_simultaneous = 1'b1;
            if (empty !== (count == 0) || full !== (count == DEPTH))
                $fatal(1, "FIFO flags disagree with occupancy: count=%0d full=%b empty=%b", count, full, empty);
            operations = operations + 1;
        end
    endtask

    integer i;
    reg rand_write;
    reg rand_read;
    reg [WIDTH-1:0] rand_data;
    initial begin
        repeat (2) @(posedge clk);
        #1;
        if (empty !== 1'b1 || full !== 1'b0 || dout !== 0) $fatal(1, "reset state incorrect");
        rst_n = 1'b1;
        cycle(1'b0, 1'b1, 8'h11); // underflow request
        cycle(1'b1, 1'b0, 8'h31);
        cycle(1'b1, 1'b1, 8'h32); // simultaneous read/write at occupancy one
        cycle(1'b0, 1'b1, 8'h00);
        cycle(1'b1, 1'b1, 8'h40); // simultaneous request at empty: write only
        cycle(1'b0, 1'b1, 8'h00);
        cycle(1'b1, 1'b0, 8'h51);
        cycle(1'b1, 1'b0, 8'h52);
        cycle(1'b1, 1'b0, 8'h53);
        cycle(1'b1, 1'b0, 8'h54); // full
        cycle(1'b1, 1'b0, 8'hee); // overflow must be rejected
        cycle(1'b1, 1'b1, 8'h55); // full boundary: read only
        cycle(1'b0, 1'b1, 8'h00);
        cycle(1'b0, 1'b1, 8'h00);
        cycle(1'b0, 1'b1, 8'h00);

        // Repeated wraparound and reproducible randomized backpressure.
        for (i = 0; i < 1200; i = i + 1) begin
            lfsr = {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};
            rand_write = lfsr[0] | lfsr[5];
            rand_read = lfsr[1] | lfsr[7];
            rand_data = lfsr[15:8] ^ i[7:0];
            cycle(rand_write, rand_read, rand_data);
        end
        while (count > 0) cycle(1'b0, 1'b1, 8'h00);
        if (!saw_simultaneous || operations < 1000) $fatal(1, "required FIFO scenarios were not exercised");
        $display("CHECK REQ-001 PASS");
        $display("CHECK REQ-002 PASS");
        $display("CHECK REQ-003 PASS");
        $display("CHECK REQ-004 PASS");
        $display("CHECK REQ-005 PASS");
        $display("PASS: sync_fifo scoreboard operations=%0d", operations);
        $finish;
    end
endmodule
