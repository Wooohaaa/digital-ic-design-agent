// Model an eight-bit count by ticking a pair of radix-16 digits.
module counter_spec(input wire clk, rst, en,
                    output wire ports_ok, relation_ok,
                    output wire [7:0] count);
    wire [7:0] q;
    counter dut(.clk(clk), .rst(rst), .en(en), .q(q));
    reg [3:0] units, groups;
    always @(posedge clk) begin
        if (rst) begin
            units <= 0;
            groups <= 0;
        end else if (en) begin
            units <= units + 1'b1;
            if (&units)
                groups <= groups + 1'b1;
        end
    end
    assign count = {groups, units};
    assign ports_ok = q === count;
    // This DUT exposes its complete state as its only output.
    assign relation_ok = ports_ok;
endmodule
