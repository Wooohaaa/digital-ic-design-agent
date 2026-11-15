`timescale 1ns/1ps
class alu8_driver;
    virtual alu8_if vif;

    function new(virtual alu8_if v);
        vif = v;
    endfunction

    task drive(alu8_txn t);
        @(negedge vif.clk);
        vif.en <= 1'b1;
        vif.a  <= t.a;
        vif.b  <= t.b;
        vif.op <= t.op;
    endtask
endclass
