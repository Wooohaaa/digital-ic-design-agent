`timescale 1ns/1ps
class alu8_scoreboard;
    int unsigned checks;
    int unsigned failures;

    function new();
        checks   = 0;
        failures = 0;
    endfunction

    task automatic check(alu8_txn t, logic [7:0] got);
        logic [7:0] exp;
        exp = t.expected();
        checks++;
        if (got !== exp) begin
            failures++;
            $display("FAIL: %s got=%0d exp=%0d", t.to_s(), got, exp);
        end
    endtask
endclass
