`include "uvm_macros.svh"
import uvm_pkg::*;

class payload extends uvm_sequence_item;
    int value;
    function new(string name="payload"); super.new(name); endfunction
endclass

class stimulus extends uvm_sequence#(payload);
    bit restart, second_completed;
    int restart_depth, fresh_response, fresh_id;
    function new(string name="stimulus"); super.new(name); endfunction
    task send_value(int value);
        payload item=new("item");
        start_item(item);
        item.value=value;
        item.set_transaction_id(7);
        finish_item(item);
    endtask
    function int pending_responses();
        return response_queue.size();
    endfunction
    virtual function void clear_response_queue();
        super.clear_response_queue();
    endfunction
    task body();
        payload response;
        if (restart) begin
            restart_depth=pending_responses();
            send_value(21);
            get_response(response,7);
            fresh_response=response.value;
            fresh_id=response.get_transaction_id();
        end else begin
            lock();
            send_value(11);
            send_value(12);
            second_completed=1;
            #50;
            unlock();
        end
    endtask
endclass

class waiter extends uvm_sequence#(payload);
    bit requested, served;
    function new(string name="waiter"); super.new(name); endfunction
    task body();
        payload item=new("queued");
        requested=1;
        start_item(item);
        served=1;
        item.value=31;
        finish_item(item);
    endtask
endclass

class consumer extends uvm_driver#(payload);
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    task fetch();
        seq_item_port.get_next_item(req);
    endtask
    function void complete(int value);
        payload response=new("response");
        response.set_id_info(req);
        response.value=value;
        seq_item_port.item_done(response);
        req=null;
    endfunction
    function void cancel();
        req=null;
    endfunction
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
    endfunction
    function void connect_phase(uvm_phase phase);
        driver.seq_item_port.connect(seqr.seq_item_export);
    endfunction
    task run_phase(uvm_phase phase);
        stimulus reused=new("reused");
        waiter queued=new("queued");
        phase.raise_objection(this);
        fork
            reused.start(seqr);
            begin #2; queued.start(seqr); end
            begin
                driver.fetch();
                $display("OBS UVM-STOP-RESTART first %0d",driver.req.value);
                driver.complete(111);
                driver.fetch();
                #4;
                $display("OBS UVM-STOP-RESTART pending %0d %0d %0d %0d %0d",
                         driver.req.value,reused.pending_responses(),seqr.current_grabber()==reused,
                         queued.requested,seqr.is_blocked(queued));
                seqr.stop_sequences();
                driver.cancel();
            end
        join
        $display("OBS UVM-STOP-RESTART stopped %0d %0d %0d %0d %0d %0d %0d",
                 reused.get_sequence_state()==UVM_STOPPED,queued.get_sequence_state()==UVM_STOPPED,
                 seqr.is_grabbed(),seqr.has_do_available(),reused.second_completed,
                 queued.served,reused.pending_responses());
        reused.restart=1;
        fork
            reused.start(seqr);
            begin
                driver.fetch();
                $display("OBS UVM-STOP-RESTART fresh %0d %0d %0d",
                         driver.req.value,driver.req.get_transaction_id(),seqr.is_grabbed());
                driver.complete(121);
            end
        join
        $display("OBS UVM-STOP-RESTART response %0d %0d %0d %0d %0d",
                 reused.restart_depth,reused.fresh_response,reused.fresh_id,reused.pending_responses(),
                 reused.get_sequence_state()==UVM_FINISHED);
        $display("DONE UVM-STOP-RESTART");
        phase.drop_objection(this);
    endtask
endclass

module tb;
    initial run_test("profile_test");
    initial begin #100; $display("FAIL UVM-STOP-RESTART watchdog"); $finish; end
endmodule
