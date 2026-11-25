`include "uvm_macros.svh"
import uvm_pkg::*;
class hierarchy_node extends uvm_component;
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
endclass
class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    hierarchy_node branch, leaf;
    function new(string name="profile_test", uvm_component parent=null);
        super.new(name,parent);
    endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        branch = new("env",this);
        leaf = new("leaf",branch);
    endfunction
    task run_phase(uvm_phase phase);
        $display("OBS UVM-COMPONENT 0 %0d %0d %0d", branch.get_parent()==this,
                 leaf.get_parent()==branch, leaf.get_full_name()=="uvm_test_top.env.leaf");
        $display("OBS UVM-COMPONENT 1 %0d %0d %0d", branch.get_child("leaf")==leaf,
                 lookup("env.leaf")==leaf, branch.has_child("absent"));
        $display("DONE UVM-COMPONENT");
    endtask
endclass
module tb;
    initial run_test("profile_test");
endmodule
