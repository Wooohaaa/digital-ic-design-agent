`include "uvm_macros.svh"
import uvm_pkg::*;

class randomized_item extends uvm_sequence_item;
    `uvm_object_utils(randomized_item)
    rand bit [3:0] key, value;
    constraint valid_pair { key inside {[2:9]}; value == key + 4'd2; }
    function new(string name="randomized_item"); super.new(name); endfunction
endclass

class stimulus extends uvm_sequence#(randomized_item);
    `uvm_object_utils(stimulus)
    function new(string name="stimulus"); super.new(name); endfunction
    task body();
        for (int trial=0; trial<4; trial++) begin
            randomized_item item;
            int solved;
            item=randomized_item::type_id::create($sformatf("item_%0d",trial));
            item.set_transaction_id(40+trial);
            item.key=0; item.value=15;
            case (trial)
                0: solved=item.randomize();
                1: solved=item.randomize() with { key inside {4,8}; };
                2: solved=item.randomize() with { key == 1; };
                3: solved=item.randomize() with { key inside {3,7}; };
            endcase
            $display("OBS UVM-RANDOMIZED-ITEM sequence %0d %0d %0d %0d",
                     item.get_transaction_id(),solved,item.key,item.value);
            if (solved==1) begin
                start_item(item);
                finish_item(item);
            end
        end
    endtask
endclass

typedef struct packed {
    int transaction_id;
    bit [3:0] key, value;
} delivery;

class consumer extends uvm_driver#(randomized_item);
    uvm_analysis_port#(delivery) ap;
    function new(string name,uvm_component parent);
        super.new(name,parent); ap=new("ap",this);
    endfunction
    task run_phase(uvm_phase phase);
        delivery observed;
        forever begin
            seq_item_port.get_next_item(req);
            #1;
            $display("OBS UVM-RANDOMIZED-ITEM driver %0d %0d %0d",
                     req.get_transaction_id(),req.key,req.value);
            observed.transaction_id=req.get_transaction_id();
            observed.key=req.key;
            observed.value=req.value;
            ap.write(observed);
            seq_item_port.item_done();
        end
    endtask
endclass

class scoreboard extends uvm_subscriber#(delivery);
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    function void write(delivery t);
        $display("OBS UVM-RANDOMIZED-ITEM scoreboard %0d %0d %0d",
                 t.transaction_id,t.key,t.value);
    endfunction
endclass

class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    uvm_sequencer#(randomized_item) seqr;
    consumer driver;
    scoreboard score;
    function new(string name="profile_test",uvm_component parent=null);
        super.new(name,parent);
    endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        seqr=new("seqr",this); driver=new("driver",this); score=new("score",this);
    endfunction
    function void connect_phase(uvm_phase phase);
        driver.seq_item_port.connect(seqr.seq_item_export);
        driver.ap.connect(score.analysis_export);
    endfunction
    task run_phase(uvm_phase phase);
        stimulus seq;
        phase.raise_objection(this);
        seq=stimulus::type_id::create("seq"); seq.start(seqr);
        $display("DONE UVM-RANDOMIZED-ITEM");
        phase.drop_objection(this);
    endtask
endclass

module tb;
    initial run_test("profile_test");
    initial begin #100; $display("FAIL UVM-RANDOMIZED-ITEM watchdog"); $finish; end
endmodule
