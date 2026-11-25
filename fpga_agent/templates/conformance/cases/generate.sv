module lane #(parameter integer W=8)(input wire [W-1:0] a,output wire [W-1:0] y);
    for(genvar bit_index=0;bit_index<W;bit_index=bit_index+1) begin:g
        assign y[bit_index]=a[bit_index] ^ ((bit_index%2)==0);
    end
endmodule
module tb;
    localparam integer W=8;
    reg [15:0] vectors[0:63];
    reg [W-1:0] a;
    wire [W-1:0] y;
    lane #(.W(W)) dut(a,y);
    integer i;
    initial begin
        $readmemh("vectors.hex",vectors);
        for(i=0;i<64;i=i+1) begin
            a=vectors[i][15:8]; #1;
            $display("OBS SV-GENERATE %0d %0d %0d",i,y,$bits(y));
        end
        $display("DONE SV-GENERATE"); $finish;
    end
endmodule
