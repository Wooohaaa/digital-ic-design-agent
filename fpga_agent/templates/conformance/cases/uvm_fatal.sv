`include "uvm_macros.svh"
import uvm_pkg::*;
class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    function new(string name="profile_test",uvm_component parent=null);
        super.new(name,parent);
    endfunction
    task run_phase(uvm_phase phase);
        phase.raise_objection(this);
        #2;
        $display("OBS UVM-FATAL 0 1");
        `uvm_fatal("RSI_EXPECTED_FATAL","expected fatal termination")
        $display("FAIL UVM-FATAL returned after fatal");
        phase.drop_objection(this);
    endtask
endclass
module tb;
    initial run_test("profile_test");
    initial begin #100; $display("FAIL UVM-FATAL watchdog"); $finish; end
endmodule
