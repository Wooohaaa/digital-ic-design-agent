`include "uvm_macros.svh"
import uvm_pkg::*;
class config_leaf extends uvm_component;
    int value=-1, missing=-2;
    bit found, absent;
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        found = uvm_config_db#(int)::get(this,"","limit",value);
        absent = uvm_config_db#(int)::get(this,"","absent",missing);
    endfunction
endclass
class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    config_leaf left, right;
    function new(string name="profile_test",uvm_component parent=null);
        super.new(name,parent);
    endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        uvm_config_db#(int)::set(this,"left","limit",17);
        uvm_config_db#(int)::set(this,"right","limit",29);
        left=new("left",this); right=new("right",this);
    endfunction
    task run_phase(uvm_phase phase);
        $display("OBS UVM-CONFIG 0 %0d %0d %0d %0d",left.found,left.value,left.absent,left.missing);
        $display("OBS UVM-CONFIG 1 %0d %0d %0d %0d",right.found,right.value,right.absent,right.missing);
        $display("DONE UVM-CONFIG");
    endtask
endclass
module tb;
    initial run_test("profile_test");
endmodule
