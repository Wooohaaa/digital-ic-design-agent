`include "uvm_macros.svh"
import uvm_pkg::*;

interface repeat_reset_link(input logic clk);
    logic reset_n=0, in_valid=0, out_ready=0;
    wire in_ready, out_valid;
    logic [15:0] in_word=0;
    wire [15:0] out_word;
    int cycle=-1;
endinterface

class reset_item extends uvm_sequence_item;
    bit [7:0] value;
    function new(string name="reset_item"); super.new(name); endfunction
endclass

class reset_sequence extends uvm_sequence#(reset_item);
    bit exclusive, requested, granted;
    int first_value, completed;
    function new(string name="reset_sequence"); super.new(name); endfunction
    task body();
        if (exclusive) lock();
        for (int i=0; i<(exclusive ? 3 : 1); i++) begin
            reset_item item=new("item");
            item.set_transaction_id(80);
            item.value=8'(first_value+i);
            requested=1;
            start_item(item);
            granted=1;
            finish_item(item);
            completed++;
        end
        if (exclusive) unlock();
    endtask
endclass

class reset_driver extends uvm_driver#(reset_item);
    virtual repeat_reset_link bus;
    uvm_sequencer#(reset_item) seqr;
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    task run_phase(uvm_phase phase);
        bit accepted;
        wait (bus.reset_n);
        forever begin
            seq_item_port.get_next_item(req);
            @(negedge bus.clk);
            bus.in_word={8'(req.get_transaction_id()),req.value};
            bus.in_valid=1;
            do @(posedge bus.clk); while (bus.reset_n && !bus.in_ready);
            accepted=bus.reset_n && bus.in_ready;
            if (!accepted) begin
                $display("OBS UVM-FIFO-REPEAT-RESET cancel %0d %0d",bus.cycle,bus.in_word);
                seqr.stop_sequences();
            end
            @(negedge bus.clk);
            bus.in_valid=0;
            if (accepted) seq_item_port.item_done();
        end
    endtask
endclass

typedef struct packed {
    int cycle;
    logic [15:0] word;
} reset_delivery;

class reset_monitor extends uvm_monitor;
    virtual repeat_reset_link bus;
    uvm_analysis_port#(reset_delivery) ap;
    function new(string name,uvm_component parent);
        super.new(name,parent); ap=new("ap",this);
    endfunction
    task run_phase(uvm_phase phase);
        reset_delivery sample;
        forever begin
            @(posedge bus.clk);
            if (bus.cycle>=0)
                $display("OBS UVM-FIFO-REPEAT-RESET edge %0d %0d %0d %0d %0d %0d %0d %0d",
                         bus.cycle,bus.reset_n,bus.in_valid,bus.in_ready,bus.in_word,
                         bus.out_valid,bus.out_ready,bus.out_word);
            if (bus.reset_n && bus.out_valid && bus.out_ready) begin
                sample.cycle=bus.cycle; sample.word=bus.out_word; ap.write(sample);
            end
        end
    endtask
endclass

class reset_scoreboard extends uvm_subscriber#(reset_delivery);
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    function void write(reset_delivery t);
        $display("OBS UVM-FIFO-REPEAT-RESET score %0d %0d",t.cycle,t.word);
    endfunction
endclass

class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    virtual repeat_reset_link bus;
    uvm_sequencer#(reset_item) seqr;
    reset_driver driver;
    reset_monitor mon;
    reset_scoreboard score;
    function new(string name="profile_test",uvm_component parent=null); super.new(name,parent); endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        if (!uvm_config_db#(virtual repeat_reset_link)::get(this,"","bus",bus))
            `uvm_fatal("NOBUS","FIFO reset interface missing")
        seqr=new("seqr",this); driver=new("driver",this);
        mon=new("mon",this); score=new("score",this);
        driver.bus=bus; driver.seqr=seqr; mon.bus=bus;
    endfunction
    function void connect_phase(uvm_phase phase);
        driver.seq_item_port.connect(seqr.seq_item_export);
        mon.ap.connect(score.analysis_export);
    endfunction
    task reset_round(int at_cycle, reset_sequence owner, reset_sequence queued);
        fork
            owner.start(seqr);
            begin
                wait (owner.completed>=1);
                queued.start(seqr);
            end
        join_none
        wait (bus.cycle==at_cycle);
        #2;
        $display("OBS UVM-FIFO-REPEAT-RESET pending %0d %0d %0d %0d %0d %0d",
                 at_cycle,seqr.current_grabber()==owner,queued.requested,
                 seqr.is_blocked(queued),owner.completed,queued.granted);
        @(posedge bus.clk);
        #1;
        $display("OBS UVM-FIFO-REPEAT-RESET stopped %0d %0d %0d %0d %0d %0d %0d %0d %0d",
                 at_cycle,owner.get_sequence_state()==UVM_STOPPED,queued.get_sequence_state()==UVM_STOPPED,
                 seqr.is_grabbed(),seqr.has_do_available(),owner.completed,queued.completed,
                 bus.out_valid,bus.out_word);
    endtask
    task run_phase(uvm_phase phase);
        reset_sequence first=new("first");
        reset_sequence first_waiter=new("first_waiter");
        reset_sequence second=new("second");
        reset_sequence second_waiter=new("second_waiter");
        reset_sequence fresh_a=new("fresh_a");
        reset_sequence fresh_b=new("fresh_b");
        first.exclusive=1; first.first_value=10; first_waiter.first_value=90;
        second.exclusive=1; second.first_value=20; second_waiter.first_value=91;
        fresh_a.first_value=100; fresh_b.first_value=101;
        phase.raise_objection(this);
        reset_round(7,first,first_waiter);
        wait (bus.cycle==8);
        reset_round(17,second,second_waiter);
        wait (bus.cycle==18);
        fork
            begin
                fresh_a.start(seqr);
                fresh_b.start(seqr);
            end
        join_none
        wait (bus.cycle==25);
        @(posedge bus.clk);
        @(negedge bus.clk);
        $display("OBS UVM-FIFO-REPEAT-RESET completed %0d %0d %0d %0d %0d %0d",
                 fresh_a.completed,fresh_b.completed,
                 fresh_a.get_sequence_state()==UVM_FINISHED,fresh_b.get_sequence_state()==UVM_FINISHED,
                 seqr.is_grabbed(),seqr.has_do_available());
        $display("DONE UVM-FIFO-REPEAT-RESET");
        phase.drop_objection(this);
    endtask
endclass

module tb;
    bit clk;
    always #5 clk=~clk;
    repeat_reset_link bus(clk);
    fifo_two dut(clk,bus.reset_n,bus.in_valid,bus.out_ready,bus.in_word,
                 bus.in_ready,bus.out_valid,bus.out_word);
    initial begin
        uvm_config_db#(virtual repeat_reset_link)::set(null,"uvm_test_top","bus",bus);
        run_test("profile_test");
    end
    initial begin
        repeat (2) @(negedge clk);
        for (int cycle=0; cycle<26; cycle++) begin
            bus.cycle=cycle;
            bus.reset_n=1;
            bus.out_ready=cycle==7 || cycle==17 || cycle>=24;
            if (cycle==7 || cycle==17) begin
                #3; bus.reset_n=0;
            end
            @(negedge clk);
        end
    end
    initial begin #400; $display("FAIL UVM-FIFO-REPEAT-RESET watchdog"); $finish; end
endmodule
