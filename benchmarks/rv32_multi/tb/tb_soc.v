`timescale 1ns/1ps
module tb_soc;
reg clk=0,rst_n=0; wire out;
always #10 clk=~clk;
rv32_soc dut(.I_sys_clk(clk),.I_rst_n(rst_n),.O_screen_pwm(out));
initial begin repeat(3) @(negedge clk); rst_n=1;
 wait(dut.halted); repeat(2) @(negedge clk);
 if(dut.trap!==0) $fatal(1,"SoC unexpected trap");
 if(dut.signature!==32'h563149ef) $fatal(1,"SoC signature mismatch %08x",dut.signature);
 $display("PASS: board ROM/RAM signature");$finish;
end
initial begin #1000000;$fatal(1,"SoC timeout");end
endmodule
