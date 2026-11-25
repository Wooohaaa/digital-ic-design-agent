// Packed word: transaction ID [15:8], key [7:4], value [3:0].
module fifo_two(
    input logic clk, reset_n, in_valid, out_ready,
    input wire [15:0] in_word,
    output wire in_ready, out_valid,
    output wire [15:0] out_word
);
    logic [15:0] storage[2];
    logic read_index, write_index;
    logic [1:0] used;
    wire push=in_valid && in_ready, pop=out_valid && out_ready;
    assign in_ready=used<2;
    assign out_valid=used!=0;
    assign out_word=out_valid ? storage[read_index] : '0;
    always_ff @(posedge clk) begin
        if (!reset_n) begin
            read_index<=0; write_index<=0; used<=0;
        end else begin
            if (push) begin
                storage[write_index]<=in_word;
                write_index<=~write_index;
            end
            if (pop) read_index<=~read_index;
            case ({push,pop})
                2'b10: used<=used+1'b1;
                2'b01: used<=used-1'b1;
                default: ;
            endcase
        end
    end
endmodule
