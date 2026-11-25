`timescale 1ns/1ps
module tb_adder;
    reg [7:0] a, b;
    reg cin;
    wire [7:0] sum;
    wire cout;
    integer i, j, k, expected;
    adder dut(a, b, cin, sum, cout);
    initial begin
        // Exhaust the complete 17-bit input space, including carry boundaries.
        for (i=0; i<256; i=i+1)
            for (j=0; j<256; j=j+1)
                for (k=0; k<2; k=k+1) begin
                    a=i[7:0]; b=j[7:0]; cin=k[0]; expected=i+j+k;
                    #1;
                    if ({cout,sum} !== expected[8:0]) $fatal(1,"adder mismatch");
                end
        $display("PASS: 131072 exhaustive adder inputs");
        $finish;
    end
endmodule
