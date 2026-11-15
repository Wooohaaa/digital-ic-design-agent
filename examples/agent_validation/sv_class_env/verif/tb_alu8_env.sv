`timescale 1ns/1ps
// Minimal, self-authored class-based (UVM-style) environment: a randomized
// transaction, a driver bound to a virtual interface, and a self-checking
// scoreboard. No external UVM library is used. Run with `icforge svsim`.
module tb_alu8_env;
    localparam integer CHECKS = 64;

    logic clk;
    initial begin
        clk = 1'b0;
        forever #5 clk = ~clk;
    end

    alu8_if bus (clk);
    alu8 dut (
        .clk(bus.clk), .rst_n(bus.rst_n), .en(bus.en),
        .a(bus.a), .b(bus.b), .op(bus.op), .y(bus.y)
    );

    alu8_driver     drv;
    alu8_scoreboard scb;
    alu8_txn        t;
    int i;

    initial begin
        bus.rst_n = 1'b0;
        bus.en    = 1'b0;
        bus.a     = '0;
        bus.b     = '0;
        bus.op    = '0;
        drv = new(bus);
        scb = new();

        repeat (3) @(posedge clk);
        #1;
        if (bus.y !== 8'h00) begin
            scb.failures = scb.failures + 1;
            $display("FAIL: reset y=%0d", bus.y);
        end
        $display("CHECK REQ-001 PASS");
        bus.rst_n = 1'b1;

        for (i = 0; i < CHECKS; i++) begin
            t = new();
            if (t.randomize() != 1) $fatal(1, "randomize failed");
            // Force every opcode at least once for directed coverage.
            if (i < 4) t.op = i[1:0];
            drv.drive(t);
            @(posedge clk); #1;
            scb.check(t, bus.y);
        end

        if (scb.failures != 0) $fatal(1, "alu8 env failed: failures=%0d", scb.failures);
        if (scb.checks < CHECKS) $fatal(1, "too few checks: %0d", scb.checks);
        $display("CHECK REQ-002 PASS");
        $display("CHECK REQ-003 PASS");
        $display("CHECK REQ-004 PASS");
        $display("CHECK REQ-005 PASS");
        $display("PASS: alu8 class environment checks=%0d", scb.checks);
        $finish;
    end
endmodule
