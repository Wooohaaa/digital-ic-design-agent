`include "uvm_macros.svh"
import uvm_pkg::*;
class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    function new(string name="profile_test",uvm_component parent=null);
        super.new(name,parent);
    endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        $display("OBS UVM-PHASE 0 1");
    endfunction
    task run_phase(uvm_phase phase);
        phase.raise_objection(this);
        #2; $display("OBS UVM-PHASE 1 1");
        phase.drop_objection(this);
    endtask
    function void report_phase(uvm_phase phase);
        $display("OBS UVM-PHASE 2 1");
        $display("DONE UVM-PHASE");
    endfunction
endclass
module tb;
    initial run_test("profile_test");
endmodule
