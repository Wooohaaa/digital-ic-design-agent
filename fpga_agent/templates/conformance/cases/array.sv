module tb;
    reg [7:0] mem[0:7];
    reg [15:0] vectors[0:63];
    integer i;
    initial begin
        for(i=0;i<8;i=i+1) mem[i]=0;
        $readmemh("vectors.hex",vectors);
        for(i=0;i<64;i=i+1) begin
            mem[i%8]=vectors[i][15:8] ^ vectors[i][7:0];
            $display("OBS SV-ARRAY %0d %0d %0d",i,mem[i%8],mem[(i+3)%8]);
        end
        $display("DONE SV-ARRAY"); $finish;
    end
endmodule
