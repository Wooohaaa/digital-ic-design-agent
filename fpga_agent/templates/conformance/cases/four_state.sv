module tb;
    reg [3:0] values=4'bzx10;
    reg a,b;
    integer i,j;
    initial begin
        for(i=0;i<4;i=i+1) for(j=0;j<4;j=j+1) begin
            a=values[i]; b=values[j];
            $display("OBS SV-4STATE %0d %0d %b %b %b %0d",i,j,a&b,a|b,a^b,a===b);
        end
        $display("DONE SV-4STATE"); $finish;
    end
endmodule
