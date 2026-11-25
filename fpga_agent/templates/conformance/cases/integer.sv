module tb;
    reg [15:0] vectors[0:63];
    reg [7:0] a,b;
    wire [7:0] sum=a+b;
    wire [7:0] shifted=$signed(a) >>> 3;
    integer i;
    initial begin
        $readmemh("vectors.hex",vectors);
        for(i=0;i<64;i=i+1) begin
            {a,b}=vectors[i]; #1;
            $display("OBS SV-INT %0d %0d %0d %0d %0d %0d",i,sum,shifted,$signed(a)<$signed(b),a<b,a^b);
        end
        $display("DONE SV-INT"); $finish;
    end
endmodule
