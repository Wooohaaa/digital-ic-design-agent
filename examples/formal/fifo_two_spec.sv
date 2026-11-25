// Independent FIFO model: a packed list, with the oldest word in front.
// The DUT's extra ports are observational; the harness never drives its state.
module fifo_two_spec(
    input wire clk, reset_n, in_valid, out_ready,
    input wire [15:0] in_word,
    output wire ports_ok, relation_ok,
    output reg [1:0] length
);
    wire in_ready, out_valid;
    wire [15:0] out_word;
    wire [1:0] used;
    wire read_index, write_index;
    wire [15:0] slot0, slot1;
    fifo_two dut(.clk(clk), .reset_n(reset_n), .in_valid(in_valid), .out_ready(out_ready),
                 .in_word(in_word), .in_ready(in_ready), .out_valid(out_valid), .out_word(out_word),
                 .used(used), .read_index(read_index), .write_index(write_index),
                 .slot0(slot0), .slot1(slot1));

    reg [15:0] front, back;
    wire room=length<2, available=length!=0;
    wire append=room && in_valid, remove=available && out_ready;
    always @(posedge clk) begin
        if (!reset_n) begin
            length<=0;
            front<=0;
            back<=0;
        end else begin
            // Remove the head first, then append, using the pre-edge handshake.
            if (remove) begin
                front<=back;
                length<=length-1'b1;
            end
            if (append) begin
                if (length==0 || remove)
                    front<=in_word;
                else
                    back<=in_word;
                if (!remove)
                    length<=length+1'b1;
                else
                    length<=length;
            end
        end
    end

    assign ports_ok={in_ready,out_valid,out_word} === {room,available,(available ? front : 16'b0)};
    // Read and write pointers differ only at length one. Ignore unused slots.
    assign relation_ok=(length<=2) && (used===length)
        && ((read_index ^ write_index)===(length==1))
        && (!available || (front===(read_index ? slot1 : slot0)))
        && (length!=2 || (back===(read_index ? slot0 : slot1)));
endmodule
