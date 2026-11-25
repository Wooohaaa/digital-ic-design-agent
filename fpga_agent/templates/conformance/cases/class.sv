class base_item;
    int value;
    function new(int v); value=v; endfunction
    virtual function int result(); return value; endfunction
endclass
class derived_item extends base_item;
    function new(int v); super.new(v); endfunction
    function int result(); return value*2; endfunction
endclass
module tb;
    base_item item;
    derived_item child;
    initial begin
        item=new(12); $display("OBS SV-CLASS 0 %0d",item.result());
        child=new(9); $display("OBS SV-CLASS 1 %0d",child.result());
        item=child; $display("OBS SV-CLASS 2 %0d",item.result());
        $display("DONE SV-CLASS"); $finish;
    end
endmodule
