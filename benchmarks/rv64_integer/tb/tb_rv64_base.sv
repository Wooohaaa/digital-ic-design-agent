`timescale 1ns/1ps
module tb_rv64_base;
    parameter MACHINE = 0;
    reg clk = 0;
    reg rst_n = 0;
    reg [31:0] image [0:1023];
    reg [63:0] ram [0:127];
    wire [63:0] fetch_pc, retire_pc, retire_value, data_address, data_write_value;
    wire [31:0] retire_instruction;
    wire [4:0] retire_rd;
    wire [7:0] data_write_mask;
    wire data_valid, retire_valid, halted, trap;
    integer programs, words, budget, fd, id, i, cycle, lane;
    reg [63:0] payload;
    wire instruction_fault = fetch_pc >= words * 4;
    wire [31:0] instruction = instruction_fault ? 32'h0 : image[fetch_pc[11:2]];
    wire data_fault = data_address < 64'h1000 || data_address >= 64'h1400;
    wire [63:0] data_read = data_fault ? 64'h0 : ram[data_address[9:3]];

`ifdef NETLIST
    rv64_integer dut (.*);
`else
    rv64_integer #(.FULL_BASE(1), .MACHINE(MACHINE)) dut (.*);
`endif

    always #5 clk = !clk;
    always @(posedge clk) begin
        if (rst_n && data_write_mask != 0) begin
            if (!data_valid || data_fault)
                $fatal(1, "write without an in-range request");
            payload = 0;
            for (lane = 0; lane < 8; lane = lane + 1) begin
                if (data_write_mask[lane]) begin
                    payload[lane*8 +: 8] = data_write_value[lane*8 +: 8];
                    ram[data_address[9:3]][lane*8 +: 8] <= data_write_value[lane*8 +: 8];
                end
            end
            $display("W %016h %02h %016h", {data_address[63:3], 3'b0}, data_write_mask, payload);
        end
    end

    initial begin
        fd = $fopen("programs.txt", "r");
        if (!fd || $fscanf(fd, "%d", programs) != 1)
            $fatal(1, "missing fixture");
        for (id = 0; id < programs; id = id + 1) begin
            if ($fscanf(fd, "%d %d", words, budget) != 2 || words < 1 || words > 1024 || budget < 4)
                $fatal(1, "invalid fixture dimensions");
            for (i = 0; i < words; i = i + 1)
                if ($fscanf(fd, "%h", image[i]) != 1)
                    $fatal(1, "bad instruction");
            for (i = 0; i < 128; i = i + 1)
                ram[i] = 0;
            rst_n = 0;
            @(negedge clk);
            $display("C %0d", id);
            if (fetch_pc !== 0 || retire_valid !== 0 || halted !== 0 || trap !== 0)
                $fatal(1, "invalid reset");
            rst_n = 1;
            for (cycle = 0; cycle < budget; cycle = cycle + 1) begin
                @(negedge clk);
                if (retire_valid)
                    $display("R %016h %08h %02h %016h %016h",
                             retire_pc, retire_instruction, retire_rd, retire_value, fetch_pc);
                if (halted)
                    cycle = budget;
                else if (trap)
                    $display("T %016h", fetch_pc);
                else if (retire_valid !== 1)
                    $fatal(1, "cycle with no retirement or trap");
            end
            if (halted !== 1)
                $fatal(1, "watchdog expired");
            repeat (3) begin
                $display("H %016h %b %b %b", fetch_pc, halted, trap, data_valid);
                @(negedge clk);
                if (retire_valid !== 0)
                    $fatal(1, "retired while halted");
            end
            for (i = 0; i < 128; i = i + 1)
                if (ram[i] !== 0)
                    $display("D %04h %016h", 16'h1000 + 16'(i * 8), ram[i]);
        end
        $fclose(fd);
        $finish(0);
    end
endmodule
