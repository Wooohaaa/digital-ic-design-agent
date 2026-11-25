`timescale 1ns/1ps
module tb_act;
    localparam integer DATA_BASE = 'h200000;
    localparam integer RAM_BYTES = 'h210000;
    reg clk = 0, rst_n = 0;
    always #5 clk = ~clk;

    wire [31:0] ia, da, dw, dr, ir, rpc, ri;
    wire dv, rv, halted, trap;
    wire [3:0] ds;
    reg [31:0] mem [0:RAM_BYTES/4-1];
    reg [1023:0] image;
    reg [31:0] signature_begin, signature_end, halt_pc;
    integer lane, address, cycles = 0;

    assign ir = ia < DATA_BASE ? mem[ia[20:2]] : 32'hffffffff;
    assign dr = da < RAM_BYTES ? mem[da[21:2]] : 32'h0;
    rv32_core dut(
        .clk(clk), .rst_n(rst_n), .imem_addr(ia), .imem_rdata(ir),
        .dmem_valid(dv), .dmem_wstrb(ds), .dmem_addr(da), .dmem_wdata(dw), .dmem_rdata(dr),
        .retire_valid(rv), .retire_pc(rpc), .retire_insn(ri), .retire_rd(), .retire_wdata(),
        .halted(halted), .trap(trap)
    );

    initial begin
        if (!$value$plusargs("IMAGE=%s", image) ||
            !$value$plusargs("BEGIN=%h", signature_begin) ||
            !$value$plusargs("END=%h", signature_end) ||
            !$value$plusargs("HALT=%h", halt_pc))
            $fatal(1, "Missing ACT fixture arguments");
        if (signature_begin < DATA_BASE || signature_end > RAM_BYTES ||
            signature_begin >= signature_end || signature_begin[1:0] != 0 ||
            signature_end[1:0] != 0 || halt_pc >= DATA_BASE || halt_pc[1:0] != 0)
            $fatal(1, "Invalid ACT fixture layout");
        for (address = DATA_BASE/4; address < RAM_BYTES/4; address = address + 1)
            mem[address] = 0;
        $readmemh(image, mem);
        repeat (3) @(negedge clk);
        rst_n <= 1;
    end

    always @(posedge clk) begin
        if (rst_n && dv === 1'b1) begin
            if ((^{da, ds}) === 1'bx || da >= RAM_BYTES)
                $fatal(1, "Unknown or out-of-range memory request");
            if (ds != 0) begin
                if (da < DATA_BASE || (^dw) === 1'bx)
                    $fatal(1, "Unknown write or write to test text");
                for (lane = 0; lane < 4; lane = lane + 1)
                    if (ds[lane])
                        mem[da[21:2]][8*lane +: 8] <= dw[8*lane +: 8];
            end
        end
    end

    always @(negedge clk) if (rst_n) begin
        cycles = cycles + 1;
        if (halted === 1'b1) begin
            if ({trap, rv, rpc, ri} !== {1'b0, 1'b1, halt_pc, 32'h00100073})
                $fatal(1, "Expected a retired EBREAK at the harness halt marker");
            for (address = signature_begin; address < signature_end; address = address + 4)
                $display("SIGNATURE %08x %08x", address, mem[address/4]);
            $display("DONE %0d", cycles);
            $finish;
        end
        if (cycles > 800000)
            $fatal(1, "ACT watchdog timeout");
    end
endmodule
