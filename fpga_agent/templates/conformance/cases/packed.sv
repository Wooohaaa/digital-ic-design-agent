module tb;
    typedef struct packed {logic [3:0] hi,lo;} pair_t;
    typedef enum logic [1:0] {IDLE=0, BUSY=2} state_t;
    pair_t pair;
    state_t state;
    reg [15:0] vectors[0:63];
    integer i;
    initial begin
        $readmemh("vectors.hex",vectors); state=BUSY;
        for(i=0;i<64;i=i+1) begin
            pair=vectors[i][15:8];
            $display("OBS SV-PACKED %0d %0d %0d %0d %0d %0d",i,pair.hi,pair.lo,$bits(pair),$bits(state),state);
        end
        $display("DONE SV-PACKED"); $finish;
    end
endmodule
