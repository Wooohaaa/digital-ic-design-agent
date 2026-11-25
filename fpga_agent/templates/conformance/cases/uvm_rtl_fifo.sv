`include "uvm_macros.svh"
import uvm_pkg::*;

typedef struct packed {
    bit [7:0] id;
    bit [3:0] key, value;
} fifo_word;

interface fifo_link(input logic clk);
    logic reset_n=0, in_valid=0, out_ready=0;
    wire in_ready, out_valid;
    fifo_word in_word='0, out_word;
    int cycle=-1;
endinterface

class fifo_item extends uvm_sequence_item;
    rand bit [3:0] key, value;
    constraint valid_pair { key inside {[2:9]}; value == key + 4'd2; }
    function new(string name="fifo_item"); super.new(name); endfunction
endclass

class fifo_stimulus extends uvm_sequence#(fifo_item);
    int completed;
    function new(string name="fifo_stimulus"); super.new(name); endfunction
    task body();
        for (int trial=0; trial<6; trial++) begin
            fifo_item item=new("request");
            int solved;
            item.set_transaction_id(60+trial);
            item.key=0; item.value=15;
            case (trial)
                1: solved=item.randomize() with { key inside {4,8}; };
                2: solved=item.randomize() with { key == 1; };
                4: solved=item.randomize() with { key inside {3,7}; };
                default: solved=item.randomize();
            endcase
            $display("OBS UVM-RTL-FIFO solution %0d %0d %0d %0d",
                     item.get_transaction_id(),solved,item.key,item.value);
            if (solved==1) begin
                start_item(item);
                finish_item(item);
                completed++;
            end
        end
    endtask
endclass

class fifo_driver extends uvm_driver#(fifo_item);
    virtual fifo_link bus;
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    task run_phase(uvm_phase phase);
        wait (bus.reset_n);
        forever begin
            seq_item_port.get_next_item(req);
            @(negedge bus.clk);
            bus.in_word={8'(req.get_transaction_id()),req.key,req.value};
            bus.in_valid=1;
            do @(posedge bus.clk); while (!bus.in_ready);
            @(negedge bus.clk);
            bus.in_valid=0;
            seq_item_port.item_done();
        end
    endtask
endclass

typedef struct packed {
    int cycle;
    fifo_word word;
} fifo_delivery;

class fifo_monitor extends uvm_monitor;
    virtual fifo_link bus;
    uvm_analysis_port#(fifo_delivery) ap;
    function new(string name,uvm_component parent);
        super.new(name,parent); ap=new("ap",this);
    endfunction
    task run_phase(uvm_phase phase);
        fifo_delivery sample;
        forever begin
            @(posedge bus.clk);
            if (bus.reset_n) begin
                $display("OBS UVM-RTL-FIFO edge %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",
                         bus.cycle,bus.in_valid,bus.in_ready,bus.in_word.id,bus.in_word.key,bus.in_word.value,
                         bus.out_valid,bus.out_ready,bus.out_word.id,bus.out_word.key,bus.out_word.value);
                if (bus.out_valid && bus.out_ready) begin
                    sample.cycle=bus.cycle;
                    sample.word=bus.out_word;
                    ap.write(sample);
                end
            end
        end
    endtask
endclass

class fifo_scoreboard extends uvm_subscriber#(fifo_delivery);
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    function void write(fifo_delivery t);
        $display("OBS UVM-RTL-FIFO score %0d %0d %0d %0d",t.cycle,t.word.id,t.word.key,t.word.value);
    endfunction
endclass

class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    virtual fifo_link bus;
    uvm_sequencer#(fifo_item) seqr;
    fifo_driver driver;
    fifo_monitor mon;
    fifo_scoreboard score;
    function new(string name="profile_test",uvm_component parent=null); super.new(name,parent); endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        if (!uvm_config_db#(virtual fifo_link)::get(this,"","bus",bus))
            `uvm_fatal("NOBUS","FIFO interface missing")
        seqr=new("seqr",this); driver=new("driver",this); mon=new("mon",this); score=new("score",this);
        driver.bus=bus; mon.bus=bus;
    endfunction
    function void connect_phase(uvm_phase phase);
        driver.seq_item_port.connect(seqr.seq_item_export);
        mon.ap.connect(score.analysis_export);
    endfunction
    task run_phase(uvm_phase phase);
        fifo_stimulus seq=new("seq");
        phase.raise_objection(this);
        fork
            seq.start(seqr);
        join_none
        // Keep the sample window fixed even when a defect loses a handshake.
        wait (bus.cycle==15);
        @(posedge bus.clk);
        @(negedge bus.clk);
        $display("OBS UVM-RTL-FIFO completed %0d",seq.completed);
        $display("DONE UVM-RTL-FIFO");
        phase.drop_objection(this);
    endtask
endclass

module tb;
    bit clk;
    always #5 clk=~clk;
    fifo_link bus(clk);
    fifo_two dut(clk,bus.reset_n,bus.in_valid,bus.out_ready,bus.in_word,
                 bus.in_ready,bus.out_valid,bus.out_word);
    initial begin
        uvm_config_db#(virtual fifo_link)::set(null,"uvm_test_top","bus",bus);
        run_test("profile_test");
    end
    initial begin
        repeat (2) @(negedge clk);
        bus.reset_n=1;
        for (int cycle=0; cycle<16; cycle++) begin
            bus.cycle=cycle;
            bus.out_ready=cycle>=7 && cycle!=10 && cycle!=11;
            @(negedge clk);
        end
    end
    initial begin #300; $display("FAIL UVM-RTL-FIFO watchdog"); $finish; end
endmodule
