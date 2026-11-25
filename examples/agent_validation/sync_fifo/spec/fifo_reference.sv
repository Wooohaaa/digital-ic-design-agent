// Fixed acceptance configuration: four 8-bit words, registered read output.
module sync_fifo_reference(
    input wire clk, rst_n, wr_en, rd_en,
    input wire [7:0] din,
    output wire relation_ok, ports_ok,
    output reg [2:0] count
);
    wire [7:0] dout, slot0, slot1, slot2, slot3;
    wire full, empty;
    wire [1:0] read_pointer, write_pointer;
    wire [2:0] occupancy;
    sync_fifo dut(.clk(clk), .rst_n(rst_n), .wr_en(wr_en), .rd_en(rd_en), .din(din),
                  .dout(dout), .full(full), .empty(empty),
                  .slot0(slot0), .slot1(slot1), .slot2(slot2), .slot3(slot3),
                  .read_pointer(read_pointer), .write_pointer(write_pointer), .occupancy(occupancy));

    reg [31:0] ordered, remainder;
    reg [7:0] last_read;
    wire remove=rd_en && count!=0;
    wire append=wr_en && count<4;
    wire [2:0] remaining=count-{2'b0,remove};
    always @* begin
        remainder=remove ? ordered >> 8 : ordered;
        if (append)
            case (remaining)
                0: remainder[7:0]=din;
                1: remainder[15:8]=din;
                2: remainder[23:16]=din;
                3: remainder[31:24]=din;
                default: ;
            endcase
    end
    always @(posedge clk) begin
        if (!rst_n) begin
            count<=0;
            ordered<=0;
            last_read<=0;
        end else begin
            count<=remaining+{2'b0,append};
            ordered<=remainder;
            if (remove) last_read<=ordered[7:0];
        end
    end

    // Unrotate live hardware slots to compare with the independently shifted queue.
    wire [31:0] hardware=(read_pointer==0) ? {slot3,slot2,slot1,slot0}
        : (read_pointer==1) ? {slot0,slot3,slot2,slot1}
        : (read_pointer==2) ? {slot1,slot0,slot3,slot2} : {slot2,slot1,slot0,slot3};
    wire [1:0] tail=read_pointer+count[1:0];
    assign ports_ok = {full,empty,dout} === {(count==4),(count==0),last_read};
    assign relation_ok = (count<=4) && (occupancy===count) && (write_pointer===tail)
        && (dout===last_read)  // Registered output is also state.
        && (count<1 || hardware[7:0]===ordered[7:0])
        && (count<2 || hardware[15:8]===ordered[15:8])
        && (count<3 || hardware[23:16]===ordered[23:16])
        && (count<4 || hardware[31:24]===ordered[31:24]);
endmodule
