`timescale 1ns/1ps
class alu8_txn;
    rand bit [7:0] a;
    rand bit [7:0] b;
    rand bit [1:0] op;

    function bit [7:0] expected();
        unique case (op)
            2'd0:    expected = a + b;
            2'd1:    expected = a - b;
            2'd2:    expected = a & b;
            default: expected = a | b;
        endcase
    endfunction

    function string to_s();
        return $sformatf("a=%0d b=%0d op=%0d", a, b, op);
    endfunction
endclass
