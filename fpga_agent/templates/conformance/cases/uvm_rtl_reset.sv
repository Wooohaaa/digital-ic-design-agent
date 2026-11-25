`include "uvm_macros.svh"
import uvm_pkg::*;

interface reset_link(input logic clk);
    logic reset_n=1;
    logic request_valid=0, response_ready=0;
    logic [7:0] request_data=0;
    wire request_ready, response_valid;
    wire [7:0] response_data;
endinterface

module response_slot(
    input logic clk, reset_n, request_valid, response_ready,
    input logic [7:0] request_data,
    output wire request_ready,
    output logic response_valid,
    output logic [7:0] response_data
);
    assign request_ready=reset_n && !response_valid;
    always_ff @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            response_valid<=0;
            response_data<=0;
        end else if (request_valid && request_ready) begin
            response_valid<=1;
            response_data<=request_data+8'd7;
        end else if (response_valid && response_ready) begin
            response_valid<=0;
        end
    end
endmodule

class packet extends uvm_sequence_item;
    bit [7:0] value;
    function new(string name="packet"); super.new(name); endfunction
endclass

class stimulus extends uvm_sequence#(packet);
    bit before_reset, completed;
    bit [7:0] value, result;
    int result_id;
    function new(string name="stimulus"); super.new(name); endfunction
    task body();
        packet request=new("request");
        packet response;
        if (before_reset) lock();
        start_item(request);
        request.value=value;
        request.set_transaction_id(7);
        finish_item(request);
        completed=1;
        get_response(response,7);
        result=response.value;
        result_id=response.get_transaction_id();
        if (before_reset) begin
            #50;
            unlock();
        end
    endtask
endclass

class consumer extends uvm_driver#(packet);
    virtual reset_link bus;
    uvm_sequencer#(packet) seqr;
    int cancelled, delivered;
    function new(string name,uvm_component parent); super.new(name,parent); endfunction
    function void complete(bit [7:0] value);
        packet response=new("response");
        response.set_id_info(req);
        response.value=value;
        seq_item_port.item_done(response);
        delivered++;
    endfunction
    task run_phase(uvm_phase phase);
        forever begin
            seq_item_port.get_next_item(req);
            @(negedge bus.clk);
            bus.request_valid=1;
            bus.request_data=req.value;
            do @(posedge bus.clk); while (!bus.request_ready);
            @(negedge bus.clk);
            bus.request_valid=0;
            forever begin
                @(posedge bus.clk);
                if (!bus.reset_n) begin
                    seqr.stop_sequences();
                    cancelled++;
                    break;
                end
                if (bus.response_valid && bus.response_ready) begin
                    complete(bus.response_data);
                    break;
                end
            end
            req=null;
        end
    endtask
endclass

class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    virtual reset_link bus;
    uvm_sequencer#(packet) seqr;
    consumer driver;
    function new(string name="profile_test",uvm_component parent=null); super.new(name,parent); endfunction
    function void build_phase(uvm_phase phase);
        super.build_phase(phase);
        if (!uvm_config_db#(virtual reset_link)::get(this,"","bus",bus))
            `uvm_fatal("NOBUS","Reset interface missing")
        seqr=new("seqr",this);
        driver=new("driver",this);
        driver.bus=bus;
        driver.seqr=seqr;
    endfunction
    function void connect_phase(uvm_phase phase);
        driver.seq_item_port.connect(seqr.seq_item_export);
    endfunction
    task run_phase(uvm_phase phase);
        stimulus stale=new("stale");
        stimulus fresh=new("fresh");
        phase.raise_objection(this);
        stale.value=40;
        stale.before_reset=1;
        fresh.value=90;
        #1;
        bus.reset_n=0;
        #10;
        bus.reset_n=1;
        fork
            stale.start(seqr);
            begin
                @(posedge bus.response_valid);
                #1;
                $display("OBS UVM-RTL-RESET buffered %0d %0d %0d %0d",
                         bus.response_data,bus.response_ready,driver.delivered,seqr.is_grabbed());
                #1;
                bus.reset_n=0;
                #1;
                $display("OBS UVM-RTL-RESET async %0d %0d %0d %0d",
                         bus.clk,bus.response_valid,bus.response_data,bus.request_ready);
                #11;
                $display("OBS UVM-RTL-RESET cancelled %0d %0d %0d %0d %0d",
                         driver.cancelled,driver.delivered,stale.completed,
                         stale.get_sequence_state()==UVM_STOPPED,seqr.is_grabbed());
                bus.reset_n=1;
                bus.response_ready=1;
            end
        join
        fresh.start(seqr);
        #1;
        $display("OBS UVM-RTL-RESET recovery %0d %0d %0d %0d %0d %0d",
                 fresh.completed,fresh.result,fresh.result_id,driver.delivered,driver.cancelled,bus.response_valid);
        $display("DONE UVM-RTL-RESET");
        phase.drop_objection(this);
    endtask
endclass

module tb;
    bit clk;
    always #5 clk=~clk;
    reset_link bus(clk);
    response_slot dut(bus.clk,bus.reset_n,bus.request_valid,bus.response_ready,bus.request_data,
                      bus.request_ready,bus.response_valid,bus.response_data);
    initial begin
        uvm_config_db#(virtual reset_link)::set(null,"uvm_test_top","bus",bus);
        run_test("profile_test");
    end
    initial begin #150; $display("FAIL UVM-RTL-RESET watchdog"); $finish; end
endmodule
