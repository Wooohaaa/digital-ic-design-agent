`include "uvm_macros.svh"
import uvm_pkg::*;
class profile_test extends uvm_test;
    `uvm_component_utils(profile_test)
    function new(string name="profile_test",uvm_component parent=null);
        super.new(name,parent);
    endfunction
    task run_phase(uvm_phase phase);
        uvm_report_server server;
        int infos, warnings;
        server=uvm_report_server::get_server();
        infos=server.get_severity_count(UVM_INFO); warnings=server.get_severity_count(UVM_WARNING);
        set_report_severity_id_action(UVM_INFO,"COUNT_INFO",UVM_COUNT);
        set_report_severity_id_action(UVM_WARNING,"COUNT_WARN",UVM_COUNT);
        `uvm_info("COUNT_INFO","first",UVM_NONE)
        `uvm_info("COUNT_INFO","second",UVM_NONE)
        `uvm_warning("COUNT_WARN","one warning")
        $display("OBS UVM-REPORT 0 %0d %0d %0d %0d",server.get_severity_count(UVM_INFO)-infos,
                 server.get_severity_count(UVM_WARNING)-warnings,server.get_severity_count(UVM_ERROR),
                 server.get_severity_count(UVM_FATAL));
        $display("DONE UVM-REPORT");
    endtask
endclass
module tb;
    initial run_test("profile_test");
endmodule
