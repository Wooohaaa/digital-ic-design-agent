`include "uvm_macros.svh"
import uvm_pkg::*;
class payload extends uvm_object;
    int value;
    function new(string name="payload"); super.new(name); endfunction
endclass
class observer extends uvm_subscriber#(payload);
    int seen[$];
    payload handles[$];
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    function void write(payload t); seen.push_back(t.value); handles.push_back(t); endfunction
endclass
class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    uvm_analysis_port#(payload) ap;
    observer first, second;
    function new(string name="profile_test",uvm_component parent=null);
        super.new(name,parent);
    endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        ap=new("ap",this); first=new("first",this); second=new("second",this);
    endfunction
    function void connect_phase(uvm_phase phase);
        ap.connect(first.analysis_export);
        ap.connect(second.analysis_export);
    endfunction
    task run_phase(uvm_phase phase);
        payload sent[4];
        for(int i=0;i<4;i++) begin
            sent[i]=new(); sent[i].value=3*i+5; ap.write(sent[i]);
        end
        $display("OBS UVM-ANALYSIS 0 %0d %0d",first.seen.size(),second.seen.size());
        for(int i=0;i<4;i++) begin
            if(first.seen.size()>i && second.seen.size()>i)
                $display("OBS UVM-ANALYSIS %0d %0d %0d %0d %0d",i+1,first.seen[i],second.seen[i],
                         first.handles[i]==sent[i],second.handles[i]==sent[i]);
        end
        $display("DONE UVM-ANALYSIS");
    endtask
endclass
module tb;
    initial run_test("profile_test");
endmodule
