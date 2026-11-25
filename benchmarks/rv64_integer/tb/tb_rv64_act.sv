`timescale 1ns/1ps
module tb_rv64_act;
    localparam integer DATA_BASE = 'h200000;
    localparam integer RAM_END = 'h210000;
    reg clk = 0, rst_n = 0;
    reg [31:0] text [0:DATA_BASE/4-1];
    reg [63:0] ram [0:(RAM_END-DATA_BASE)/8-1];
    wire [63:0] fetch_pc, retire_pc, retire_value, data_address, data_write_value;
    wire [31:0] retire_instruction;
    wire [4:0] retire_rd;
    wire [7:0] data_write_mask;
    wire data_valid, retire_valid, halted, trap;
    reg [31:0] signature_begin, signature_end, halt_pc, text_end;
    wire instruction_fault = fetch_pc >= {32'b0, text_end};
    wire [31:0] instruction = instruction_fault ? 0 : text[fetch_pc[20:2]];
    wire data_fault = data_address >= RAM_END;
    wire [63:0] data_read = data_fault ? 0 :
        data_address < DATA_BASE ? {text[{data_address[20:3], 1'b1}],
                                    text[{data_address[20:3], 1'b0}]} : ram[data_address[15:3]];
    integer lane, address, cycles = 0;
    reg [2047:0] text_file, data_file;
    reg [31:0] word;

`ifdef NETLIST
    rv64_integer dut (.*);
`else
    rv64_integer #(.FULL_BASE(1)) dut (.*);
`endif

    always #5 clk = !clk;
    initial begin
        if (!$value$plusargs("TEXT=%s", text_file) || !$value$plusargs("DATA=%s", data_file) ||
            !$value$plusargs("BEGIN=%h", signature_begin) || !$value$plusargs("END=%h", signature_end) ||
            !$value$plusargs("HALT=%h", halt_pc) || !$value$plusargs("TEXT_END=%h", text_end))
            $fatal(1, "missing ACT64 arguments");
        if (signature_begin < DATA_BASE || signature_end > RAM_END ||
            signature_begin >= signature_end || signature_begin[1:0] || signature_end[1:0] ||
            halt_pc >= text_end || halt_pc[1:0] || text_end > DATA_BASE || text_end[1:0])
            $fatal(1, "invalid ACT64 layout");
        for (address = 0; address < (RAM_END-DATA_BASE)/8; address = address + 1)
            ram[address] = 0;
        $readmemh(text_file, text, 0, text_end/4-1);
        $readmemh(data_file, ram);
        repeat (3) @(negedge clk);
        rst_n <= 1;
    end

    always @(posedge clk) if (rst_n && data_valid && data_write_mask != 0) begin
        if (data_address < DATA_BASE || data_fault || (^{data_address, data_write_mask}) === 1'bx)
            $fatal(1, "invalid ACT64 store");
        for (lane = 0; lane < 8; lane = lane + 1)
            if (data_write_mask[lane])
                ram[data_address[15:3]][lane*8 +: 8] <= data_write_value[lane*8 +: 8];
    end

    always @(negedge clk) if (rst_n) begin
        cycles = cycles + 1;
        if (halted) begin
            if ({trap, retire_valid, retire_pc, retire_instruction} !==
                {1'b0, 1'b1, 32'b0, halt_pc, 32'h00100073})
                $fatal(1, "ACT64 stopped at fetch=%h retire=%h instruction=%h trap=%b",
                       fetch_pc, retire_pc, retire_instruction, trap);
            for (address = signature_begin; address < signature_end; address = address + 4) begin
                if (address[2])
                    word = ram[address[15:3]][63:32];
                else
                    word = ram[address[15:3]][31:0];
                $display("SIGNATURE %08x %08x", address, word);
            end
            $display("DONE %0d", cycles);
            $finish(0);
        end
        if (cycles > 100000)
            $fatal(1, "ACT64 watchdog timeout");
    end
endmodule
