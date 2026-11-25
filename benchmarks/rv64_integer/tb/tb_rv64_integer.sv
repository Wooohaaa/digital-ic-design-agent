`timescale 1ns/1ps
module tb_rv64_integer;
    reg clk = 0;
    reg rst_n = 0;
    reg [31:0] instruction = 0;
    wire [63:0] fetch_pc, retire_pc, retire_value;
    wire [31:0] retire_instruction;
    wire [4:0] retire_rd;
    wire retire_valid, halted, trap;
    wire instruction_fault = 0;
    wire [63:0] data_read = 0;
    wire data_fault = 0;
    wire [63:0] data_address, data_write_value;
    wire data_valid;
    wire [7:0] data_write_mask;
    rv64_integer dut (.*);

    reg [31:0] image [0:1023];
    integer programs, length, fd, id, index, cycle;
    always #5 clk = !clk;

    task automatic tick;
        @(negedge clk);
        if (retire_valid)
            $display("R %016h %08h %02h %016h", retire_pc, retire_instruction, retire_rd, retire_value);
        $display("S %016h %b %b %b", fetch_pc, halted, trap, retire_valid);
    endtask

    initial begin
        fd = $fopen("programs.txt", "r");
        if (!fd || $fscanf(fd, "%d", programs) != 1)
            $fatal(1, "missing program fixture");
        for (id = 0; id < programs; id = id + 1) begin
            if ($fscanf(fd, "%d", length) != 1 || length < 3 || length > 1024)
                $fatal(1, "bad fixture length");
            for (index = 0; index < length; index = index + 1)
                if ($fscanf(fd, "%h", image[index]) != 1)
                    $fatal(1, "bad instruction word");
            rst_n = 0;
            @(negedge clk);
            $display("C %0d", id);
            $display("S %016h %b %b %b", fetch_pc, halted, trap, retire_valid);
            rst_n = 1;
            for (cycle = 0; cycle < length + 1; cycle = cycle + 1) begin
                // Keep supplying real instructions behind the terminal instruction.
                if (^fetch_pc === 1'bx || fetch_pc[1:0] != 0 || fetch_pc >= length * 4)
                    $fatal(1, "fetch outside text");
                instruction = image[fetch_pc >> 2];
                if (halted)
                    instruction = image[length-2];
                tick();
            end
        end
        $fclose(fd);
        $finish(0);
    end
endmodule
