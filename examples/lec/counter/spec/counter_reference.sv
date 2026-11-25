module counter_reference(input wire clk, rst, en,
                         output wire relation_ok, ports_ok,
                         output reg [7:0] count);
    wire [7:0] q;
    counter_demo dut(.clk(clk), .rst(rst), .en(en), .q(q));
    always @(posedge clk) begin
        if (rst)
            count <= 0;
        else if (en)
            case (count[3:0])
                4'hf: count <= {count[7:4]+4'd1, 4'd0};
                default: count[3:0] <= count[3:0]+4'd1;
            endcase
    end
    assign ports_ok = q === count;
    assign relation_ok = ports_ok;
endmodule
