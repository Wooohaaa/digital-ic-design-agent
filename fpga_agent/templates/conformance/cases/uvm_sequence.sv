`include "uvm_macros.svh"
import uvm_pkg::*;
class payload extends uvm_sequence_item;
    `uvm_object_utils(payload)
    int value;
    function new(string name="payload"); super.new(name); endfunction
endclass
class stimulus extends uvm_sequence#(payload);
    `uvm_object_utils(stimulus)
    int completed;
    function new(string name="stimulus"); super.new(name); endfunction
    task body();
        for(int i=0;i<4;i++) begin
            payload item=payload::type_id::create("item");
            start_item(item); item.value=5*i+3; finish_item(item); completed++;
        end
    endtask
endclass
class consumer extends uvm_driver#(payload);
    int seen[$];
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    task run_phase(uvm_phase phase);
        forever begin
            seq_item_port.get_next_item(req);
            #1; seen.push_back(req.value);
            seq_item_port.item_done();
        end
    endtask
endclass
class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    uvm_sequencer#(payload) seqr;
    consumer driver;
    function new(string name="profile_test",uvm_component parent=null);
        super.new(name,parent);
    endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        seqr=new("seqr",this); driver=new("driver",this);
    endfunction
    function void connect_phase(uvm_phase phase);
        driver.seq_item_port.connect(seqr.seq_item_export);
    endfunction
    task run_phase(uvm_phase phase);
        stimulus seq;
        phase.raise_objection(this);
        seq=stimulus::type_id::create("seq"); seq.start(seqr);
        $display("OBS UVM-SEQUENCE 0 %0d %0d",seq.completed,driver.seen.size());
        foreach(driver.seen[i]) $display("OBS UVM-SEQUENCE %0d %0d",i+1,driver.seen[i]);
        $display("DONE UVM-SEQUENCE");
        phase.drop_objection(this);
    endtask
endclass
module tb;
    initial begin run_test("profile_test"); end
    initial begin #100; $display("FAIL UVM-SEQUENCE watchdog"); $finish; end
endmodule
