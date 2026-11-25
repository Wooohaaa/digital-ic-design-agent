`include "uvm_macros.svh"
import uvm_pkg::*;

interface fifo_reset_link(input logic clk);
    logic reset_n=0, in_valid=0, out_ready=0;
    wire in_ready, out_valid;
    logic [15:0] in_word=0;
    wire [15:0] out_word;
    int cycle=-1;

    task snapshot(string stage);
        $display("OBS UVM-FIFO-SYNC-RESET %s %0d %0d %0d %0d %0d %0d %0d %0d",
                 stage,cycle,reset_n,in_valid,in_ready,in_word,out_valid,out_ready,out_word);
    endtask
endinterface

class packet extends uvm_sequence_item;
    bit [7:0] value;
    function new(string name="packet"); super.new(name); endfunction
endclass

class stimulus extends uvm_sequence#(packet);
    bit fresh;
    int completed;
    function new(string name="stimulus"); super.new(name); endfunction
    task body();
        for (int i=0; i<(fresh ? 2 : 3); i++) begin
            packet item=new("item");
            item.set_transaction_id(70+(fresh ? 2*i : i));
            item.value=8'((fresh ? 100 : 10)+i);
            start_item(item);
            finish_item(item);
            completed++;
        end
    endtask
endclass

class consumer extends uvm_driver#(packet);
    virtual fifo_reset_link bus;
    uvm_sequencer#(packet) seqr;
    int cancelled;
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
                $display("OBS UVM-FIFO-SYNC-RESET cancel %0d %0d",bus.cycle,req.get_transaction_id());
                seqr.stop_sequences();
                cancelled++;
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
} delivery;

class monitor extends uvm_monitor;
    virtual fifo_reset_link bus;
    uvm_analysis_port#(delivery) ap;
    function new(string name,uvm_component parent);
        super.new(name,parent); ap=new("ap",this);
    endfunction
    task run_phase(uvm_phase phase);
        delivery sample;
        forever begin
            @(posedge bus.clk);
            if (bus.cycle>=0) bus.snapshot("edge");
            if (bus.reset_n && bus.out_valid && bus.out_ready) begin
                sample.cycle=bus.cycle; sample.word=bus.out_word;
                ap.write(sample);
            end
        end
    endtask
endclass

class scoreboard extends uvm_subscriber#(delivery);
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    function void write(delivery t);
        $display("OBS UVM-FIFO-SYNC-RESET score %0d %0d",t.cycle,t.word);
    endfunction
endclass

class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    virtual fifo_reset_link bus;
    uvm_sequencer#(packet) seqr;
    consumer driver;
    monitor mon;
    scoreboard score;
    function new(string name="profile_test",uvm_component parent=null); super.new(name,parent); endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        if (!uvm_config_db#(virtual fifo_reset_link)::get(this,"","bus",bus))
            `uvm_fatal("NOBUS","FIFO reset interface missing")
        seqr=new("seqr",this); driver=new("driver",this);
        mon=new("mon",this); score=new("score",this);
        driver.bus=bus; driver.seqr=seqr; mon.bus=bus;
    endfunction
    function void connect_phase(uvm_phase phase);
        driver.seq_item_port.connect(seqr.seq_item_export);
        mon.ap.connect(score.analysis_export);
    endfunction
    task run_phase(uvm_phase phase);
        stimulus old_seq=new("old_seq");
        stimulus fresh_seq=new("fresh_seq");
        fresh_seq.fresh=1;
        phase.raise_objection(this);
        fork
            old_seq.start(seqr);
            begin
                wait (bus.cycle==8);
                fresh_seq.start(seqr);
            end
        join_none
        wait (bus.cycle==15);
        @(posedge bus.clk);
        @(negedge bus.clk);
        $display("OBS UVM-FIFO-SYNC-RESET completed %0d %0d %0d %0d",
                 old_seq.completed,old_seq.get_sequence_state()==UVM_STOPPED,
                 driver.cancelled,fresh_seq.completed);
        $display("DONE UVM-FIFO-SYNC-RESET");
        phase.drop_objection(this);
    endtask
endclass

module tb;
    bit clk;
    always #5 clk=~clk;
    fifo_reset_link bus(clk);
    fifo_two dut(clk,bus.reset_n,bus.in_valid,bus.out_ready,bus.in_word,
                 bus.in_ready,bus.out_valid,bus.out_word);
    initial begin
        uvm_config_db#(virtual fifo_reset_link)::set(null,"uvm_test_top","bus",bus);
        run_test("profile_test");
    end
    initial begin
        repeat (2) @(negedge clk);
        bus.reset_n=1;
        for (int cycle=0; cycle<16; cycle++) begin
            bus.cycle=cycle;
            // The consumer is ready at the reset edge, but reset must win.
            bus.out_ready=cycle==7 || cycle>=12;
            if (cycle==7) begin
                #1; bus.snapshot("before");
                bus.reset_n=0;
                #1; bus.snapshot("asserted");
                @(posedge clk);
                #1; bus.snapshot("sampled");
            end
            if (cycle==8) bus.reset_n=1;
            @(negedge clk);
        end
    end
    initial begin #300; $display("FAIL UVM-FIFO-SYNC-RESET watchdog"); $finish; end
endmodule
