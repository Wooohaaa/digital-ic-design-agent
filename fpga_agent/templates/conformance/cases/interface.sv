interface bus_if;
    logic [7:0] a,b,y;
    modport device(input a,b,output y);
endinterface
module add_device(bus_if.device bus);
    assign bus.y=bus.a+bus.b;
endmodule
module tb;
    bus_if bus(); add_device dut(bus);
    reg [15:0] vectors[0:63];
    integer i;
    initial begin
        $readmemh("vectors.hex",vectors);
        for(i=0;i<64;i=i+1) begin
            {bus.a,bus.b}=vectors[i]; #1;
            $display("OBS SV-INTERFACE %0d %0d",i,bus.y);
        end
        $display("DONE SV-INTERFACE"); $finish;
    end
endmodule
