`timescale 1ns/1ps
module tb_merged_counter;
    reg clk=0, rst_n=1, en=0;
    wire [9:0] y;
    integer cycle, expected=0;
    core dut(clk, rst_n, en, y);
    always #5 clk=~clk;
    initial begin
        // Begin observation after an actual asynchronous assertion, before
        // any rising clock edge. No assertion is made about power-up state.
        #1 rst_n=0;
        #1 if(y !== 10'd0) $fatal(1,"initial async reset failed");
        #1 rst_n=1;
        for(cycle=0; cycle<2048; cycle=cycle+1) begin
            @(negedge clk);
            en=(cycle%5!=0);
            if(cycle==67 || cycle==513 || cycle==1024) begin
                #1 rst_n=0; expected=0;
                #1 if(y !== 10'd0) $fatal(1,"repeated async reset failed");
                #1 rst_n=1;
            end
            @(posedge clk);
            if(en) expected=(expected+1)%256;
            #1 if(y !== {expected[7:0],expected[1:0]}) $fatal(1,"relation mismatch at %0d",cycle);
        end
        $display("PASS: merged-state relation after async reset, 2048 cycles");
        $finish;
    end
endmodule
