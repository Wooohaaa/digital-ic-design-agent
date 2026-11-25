`include "uvm_macros.svh"
import uvm_pkg::*;

class payload extends uvm_sequence_item;
    int owner;
    int value;
    function new(string name="payload"); super.new(name); endfunction
endclass

class stimulus extends uvm_sequence#(payload);
    int owner;
    int sequence_id;
    payload responses[$];
    function new(string name="stimulus"); super.new(name); endfunction
    task body();
        for (int transaction=1; transaction<=2; transaction++) begin
            payload item=new("item");
            start_item(item);
            item.owner=owner;
            item.value=10*owner+transaction;
            item.set_transaction_id(transaction);
            finish_item(item);
            sequence_id=item.get_sequence_id();
        end
        for (int transaction=1; transaction<=2; transaction++) begin
            payload response;
            get_response(response,transaction);
            responses.push_back(response);
        end
    endtask
endclass

class consumer extends uvm_driver#(payload);
    payload accepted[$];
    payload sent[$];
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    task run_phase(uvm_phase phase);
        #4;
        repeat (6) begin
            seq_item_port.get_next_item(req);
            accepted.push_back(req);
            seq_item_port.item_done();
            #1;
        end
        for (int index=accepted.size()-1; index>=0; index--) begin
            payload response=new("response");
            response.set_id_info(accepted[index]);
            response.owner=accepted[index].owner;
            response.value=3*accepted[index].value+7;
            sent.push_back(response);
            seq_item_port.put_response(response);
            #1;
        end
    endtask
endclass

class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    uvm_sequencer#(payload) seqr;
    consumer driver;
    function new(string name="profile_test",uvm_component parent=null); super.new(name,parent); endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        seqr=new("seqr",this);
        driver=new("driver",this);
        seqr.set_arbitration(UVM_SEQ_ARB_STRICT_FIFO);
    endfunction
    function void connect_phase(uvm_phase phase);
        driver.seq_item_port.connect(seqr.seq_item_export);
    endfunction
    task run_phase(uvm_phase phase);
        stimulus low=new("low");
        stimulus high_first=new("high_first");
        stimulus high_second=new("high_second");
        stimulus sequences[3];
        phase.raise_objection(this);
        low.owner=1;
        high_first.owner=2;
        high_second.owner=3;
        sequences='{low,high_first,high_second};
        fork
            low.start(seqr,null,10);
            begin #1; high_first.start(seqr,null,100); end
            begin #2; high_second.start(seqr,null,100); end
        join
        $display("OBS UVM-ARBITRATION counts %0d %0d",driver.accepted.size(),driver.sent.size());
        foreach (driver.accepted[index]) begin
            payload item=driver.accepted[index];
            $display("OBS UVM-ARBITRATION grant %0d %0d %0d %0d",
                     index,item.owner,item.get_transaction_id(),item.value);
        end
        foreach (driver.sent[index]) begin
            payload response=driver.sent[index];
            $display("OBS UVM-ARBITRATION sent %0d %0d %0d",index,response.owner,response.get_transaction_id());
        end
        foreach (sequences[index]) begin
            stimulus seq=sequences[index];
            foreach (seq.responses[slot]) begin
                payload response=seq.responses[slot];
                $display("OBS UVM-ARBITRATION received %0d %0d %0d %0d %0d %0d",
                         seq.owner,slot,response.owner,response.get_transaction_id(),
                         response.value,response.get_sequence_id()==seq.sequence_id);
            end
        end
        $display("DONE UVM-ARBITRATION");
        phase.drop_objection(this);
    endtask
endclass

module tb;
    initial run_test("profile_test");
    initial begin #100; $display("FAIL UVM-ARBITRATION watchdog"); $finish; end
endmodule
