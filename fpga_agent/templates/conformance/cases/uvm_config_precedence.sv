`include "uvm_macros.svh"
import uvm_pkg::*;

class config_leaf extends uvm_component;
    int build_value=-1;
    bit build_found;
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        build_found=uvm_config_db#(int)::get(this,"","limit",build_value);
    endfunction
    function void observe_runtime(string step);
        int value=-1;
        bit found;
        found=uvm_config_db#(int)::get(this,"","limit",value);
        $display("OBS UVM-CONFIG-PRECEDENCE %s %s %0d %0d",step,get_full_name(),found,value);
    endfunction
    function void observe_build();
        $display("OBS UVM-CONFIG-PRECEDENCE build %s %0d %0d",get_full_name(),build_found,build_value);
    endfunction
endclass

class config_lane extends uvm_component;
    config_leaf sink, tap;
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        uvm_config_db#(int)::set(this,"sink","limit",91);
        sink=new("sink",this);
        tap=new("tap",this);
    endfunction
endclass

class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    config_lane lane_a, lane_b;
    function new(string name="profile_test",uvm_component parent=null); super.new(name,parent); endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        uvm_config_db#(int)::set(this,"lane_*.sink","limit",11);
        uvm_config_db#(int)::set(this,"lane_a.sink","limit",17);
        uvm_config_db#(int)::set(this,"lane_*.sink","limit",23);
        lane_a=new("lane_a",this);
        lane_b=new("lane_b",this);
    endfunction
    task run_phase(uvm_phase phase);
        phase.raise_objection(this);
        lane_a.sink.observe_build();
        lane_b.sink.observe_build();
        lane_a.tap.observe_build();
        #1;
        uvm_config_db#(int)::set(lane_a.sink,"","limit",41);
        lane_a.sink.observe_runtime("leaf_first");
        #1;
        uvm_config_db#(int)::set(this,"lane_a.sink","limit",53);
        lane_a.sink.observe_runtime("test_next");
        #1;
        uvm_config_db#(int)::set(lane_a.sink,"","limit",61);
        lane_a.sink.observe_runtime("leaf_last");
        lane_b.sink.observe_runtime("isolation");
        lane_a.tap.observe_runtime("isolation");
        $display("DONE UVM-CONFIG-PRECEDENCE");
        phase.drop_objection(this);
    endtask
endclass

module tb;
    initial run_test("profile_test");
    initial begin #100; $display("FAIL UVM-CONFIG-PRECEDENCE watchdog"); $finish; end
endmodule
