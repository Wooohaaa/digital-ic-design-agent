`include "uvm_macros.svh"
import uvm_pkg::*;
class base_item extends uvm_object;
    `uvm_object_utils(base_item)
    function new(string name="base_item"); super.new(name); endfunction
    virtual function int value(); return 7; endfunction
endclass
class replacement_item extends base_item;
    `uvm_object_utils(replacement_item)
    function new(string name="replacement_item"); super.new(name); endfunction
    function int value(); return 29; endfunction
endclass
class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    function new(string name="profile_test", uvm_component parent=null);
        super.new(name,parent);
    endfunction
    task run_phase(uvm_phase phase);
        base_item before_override, after_override, direct;
        replacement_item cast_item;
        bit is_replacement;
        before_override = base_item::type_id::create("before");
        base_item::type_id::set_type_override(replacement_item::get_type());
        after_override = base_item::type_id::create("after");
        direct = new("direct");
        is_replacement = $cast(cast_item,after_override);
        $display("OBS UVM-FACTORY 0 %0d %0d", before_override.value(),
                 before_override.get_type_name()=="base_item");
        $display("OBS UVM-FACTORY 1 %0d %0d %0d", after_override.value(),
                 after_override.get_type_name()=="replacement_item",is_replacement);
        $display("OBS UVM-FACTORY 2 %0d %0d", direct.value(),direct==after_override);
        $display("DONE UVM-FACTORY");
    endtask
endclass
module tb;
    initial run_test("profile_test");
endmodule
