package math_pkg;
    localparam integer BIAS=7;
    function automatic logic [7:0] rotate(input logic [7:0] value);
        return {value[6:0],value[7]};
    endfunction
endpackage
module tb;
    import math_pkg::*;
    reg [15:0] vectors[0:63];
    reg [7:0] a,biased;
    integer i;
    initial begin
        $readmemh("vectors.hex",vectors);
        for(i=0;i<64;i=i+1) begin
            a=vectors[i][15:8]; biased=a+BIAS;
            $display("OBS SV-PACKAGE %0d %0d %0d",i,rotate(a),biased);
        end
        $display("DONE SV-PACKAGE"); $finish;
    end
endmodule
