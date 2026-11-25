`timescale 1ns/1ps
module tb_counter;
    reg clk=0, rst=1, en=0;
    wire [7:0] q;
    integer cycle, total=0;
    counter_demo dut(clk, rst, en, q);
    always #5 clk=~clk;
    initial begin
        for (cycle=0; cycle<2048; cycle=cycle+1) begin
            @(negedge clk);
            rst=(cycle==0 || cycle==333 || cycle==1023);
            en=(cycle<300 || cycle%3!=0);
            @(posedge clk);
            if (rst) total=0; else if (en) total=(total+1)%256;
            #1;
            if (q !== total[7:0]) $fatal(1,"counter mismatch at cycle %0d", cycle);
        end
        $display("PASS: reset, enable, hold, rollover and repeated reset over 2048 cycles");
        $finish;
    end
endmodule
