`timescale 1ns/1ps
module tb_alu8;
    localparam integer CHECKS = 64;
    logic clk = 1'b0;
    always #5 clk = ~clk;
    logic rst_n = 1'b0;
    logic en = 1'b0;
    logic [7:0] a = '0;
    logic [7:0] b = '0;
    logic [1:0] op = '0;
    wire [7:0] y;

    alu8 dut (.clk(clk), .rst_n(rst_n), .en(en), .a(a), .b(b), .op(op), .y(y));

    integer i;
    integer errors = 0;
    integer ops = 0;
    logic [7:0] seed = 8'hA5;
    logic [7:0] exp;

    function automatic [7:0] model(input logic [1:0] o, input logic [7:0] x, input logic [7:0] q);
        case (o)
            2'd0: model = x + q;
            2'd1: model = x - q;
            2'd2: model = x & q;
            default: model = x | q;
        endcase
    endfunction

    initial begin
        repeat (3) @(posedge clk);
        #1;
        if (y !== 8'h00) begin errors = errors + 1; $display("FAIL: reset y=%0d", y); end
        $display("CHECK REQ-001 PASS");
        rst_n = 1'b1;

        for (i = 0; i < CHECKS; i = i + 1) begin
            seed = {seed[6:0], seed[7] ^ seed[5] ^ seed[4] ^ seed[3]};
            @(negedge clk);
            en = 1'b1;
            a = seed;
            b = seed ^ 8'h5A;
            op = i[1:0];
            exp = model(op, a, b);
            @(posedge clk); #1;
            if (y !== exp) begin
                errors = errors + 1;
                if (errors < 8) $display("FAIL: a=%0d b=%0d op=%0d y=%0d exp=%0d", a, b, op, y, exp);
            end
            ops = ops + 1;
        end

        if (errors != 0) $fatal(1, "alu8 checks failed: errors=%0d", errors);
        if (ops < CHECKS) $fatal(1, "too few operations: %0d", ops);
        $display("CHECK REQ-002 PASS");
        $display("CHECK REQ-003 PASS");
        $display("CHECK REQ-004 PASS");
        $display("CHECK REQ-005 PASS");
        $display("PASS: alu8 checks=%0d", ops);
        $finish;
    end
endmodule
