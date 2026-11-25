module core(input clk, rst_n, en, output [9:0] y);
    reg [7:0] count;
    reg [1:0] low;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin count <= 0; low <= 0; end
        else if (en) begin count <= count + 1; low <= count[1:0] + 1; end
    end
    assign y = {count, low};
endmodule
