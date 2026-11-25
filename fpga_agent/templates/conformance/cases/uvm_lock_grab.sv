`include "uvm_macros.svh"
import uvm_pkg::*;

class payload extends uvm_sequence_item;
    int value;
    function new(string name="payload"); super.new(name); endfunction
endclass

class stimulus extends uvm_sequence#(payload);
    int owner;
    bit acquired, released;
    function new(string name="stimulus"); super.new(name); endfunction
    task send_value(int value);
        payload item=new("item");
        start_item(item);
        item.value=value;
        finish_item(item);
    endtask
    task body();
        case (owner)
            1: begin
                lock();
                acquired=has_lock();
                #6;
                send_value(11);
                #3;
                send_value(12);
                if (has_lock()) unlock();
                released=!has_lock();
            end
            2: begin
                lock();
                acquired=has_lock();
                send_value(21);
                unlock();
                released=!has_lock();
            end
            3: begin
                grab();
                acquired=has_lock();
                send_value(31);
                ungrab();
                released=!has_lock();
            end
            4: send_value(41);
        endcase
    endtask
endclass

class consumer extends uvm_driver#(payload);
    uvm_sequencer#(payload) seqr;
    int values[$];
    int held[$];
    int owner_match[$];
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    task run_phase(uvm_phase phase);
        forever begin
            seq_item_port.get_next_item(req);
            values.push_back(req.value);
            held.push_back(int'(seqr.is_grabbed()));
            owner_match.push_back(int'(seqr.current_grabber()==req.get_parent_sequence()));
            #1;
            seq_item_port.item_done();
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
        driver.seqr=seqr;
    endfunction
    function void connect_phase(uvm_phase phase);
        driver.seq_item_port.connect(seqr.seq_item_export);
    endfunction
    task run_phase(uvm_phase phase);
        stimulus holder=new("holder");
        stimulus waiter=new("waiter");
        stimulus grabber=new("grabber");
        stimulus ordinary=new("ordinary");
        phase.raise_objection(this);
        holder.owner=1;
        waiter.owner=2;
        grabber.owner=3;
        ordinary.owner=4;
        fork
            holder.start(seqr);
            begin #1; ordinary.start(seqr); end
            begin #2; waiter.start(seqr); end
            begin #3; grabber.start(seqr); end
            begin
                #4;
                $display("OBS UVM-LOCK-GRAB queued %0d %0d %0d %0d",
                         seqr.current_grabber()==holder,seqr.is_blocked(waiter),
                         seqr.is_blocked(grabber),seqr.is_blocked(ordinary));
            end
        join
        foreach (driver.values[index])
            $display("OBS UVM-LOCK-GRAB grant %0d %0d %0d %0d",
                     index,driver.values[index],driver.held[index],driver.owner_match[index]);
        $display("OBS UVM-LOCK-GRAB ownership 1 %0d %0d",holder.acquired,holder.released);
        $display("OBS UVM-LOCK-GRAB ownership 2 %0d %0d",waiter.acquired,waiter.released);
        $display("OBS UVM-LOCK-GRAB ownership 3 %0d %0d",grabber.acquired,grabber.released);
        $display("OBS UVM-LOCK-GRAB remaining %0d %0d",seqr.is_grabbed(),seqr.current_grabber()!=null);
        $display("DONE UVM-LOCK-GRAB");
        phase.drop_objection(this);
    endtask
endclass

module tb;
    initial run_test("profile_test");
    initial begin #100; $display("FAIL UVM-LOCK-GRAB watchdog"); $finish; end
endmodule
