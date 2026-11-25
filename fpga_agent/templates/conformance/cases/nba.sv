module tb;
    reg clk=0;
    reg [7:0] a=0,b=0;
    integer i;
    always @(posedge clk) begin a<=a+1; b<=a; end
    initial begin
        for(i=0;i<32;i=i+1) begin
            #1 clk=1; #1;
            $display("OBS SV-NBA %0d %0d %0d",i,a,b);
            #1 clk=0;
        end
        $display("DONE SV-NBA"); $finish;
    end
endmodule
