`timescale 1ns/1ps
module tb_cpu;
reg clk=0, rst_n=0;
always #10 clk=~clk;
wire [31:0] ia, ir, da, dw, dr, rpc, ri, rw;
wire dv, rv, halted, trap;
wire [3:0] ds;
wire [4:0] rd;
reg [31:0] mem[0:4095];
reg [1023:0] image;
integer k, lane, cycles=0, limit=24000;
assign ir = ia < 16384 ? mem[ia[13:2]] : 32'hffffffff;
assign dr = da < 16384 ? mem[da[13:2]] : 32'h0;
rv32_core dut(.clk(clk),.rst_n(rst_n),.imem_addr(ia),.imem_rdata(ir),
 .dmem_valid(dv),.dmem_wstrb(ds),.dmem_addr(da),.dmem_wdata(dw),.dmem_rdata(dr),
 .retire_valid(rv),.retire_pc(rpc),.retire_insn(ri),.retire_rd(rd),.retire_wdata(rw),.halted(halted),.trap(trap));
initial begin
 if (!$value$plusargs("IMAGE=%s",image)) $fatal(1,"missing IMAGE");
 if ($value$plusargs("LIMIT=%d",limit)) begin end
 $readmemh(image,mem);
 repeat(3) @(negedge clk);
 rst_n=1;
end
always @(posedge clk) begin
 if (!rst_n && dv===1'b1 && ds!==4'b0000) $fatal(1,"store during reset");
 if (rst_n && dv===1'b1 && ds!==4'b0000) begin
  if ((^{da,ds,dw}) === 1'bx || da>=16384) $fatal(1,"invalid store bus");
  $display("STORE %08x %01x %08x",da,ds,dw);
  for(lane=0;lane<4;lane=lane+1) if(ds[lane]) mem[da[13:2]][8*lane+:8]<=dw[8*lane+:8];
 end
end
always @(negedge clk) if(rst_n) begin
 cycles=cycles+1;
 if(rv!==1'b0 && rv!==1'b1) $fatal(1,"unknown retire valid");
 if(rv) begin
  if((^{rpc,ri,rd,rw})===1'bx) $fatal(1,"unknown retirement");
  $display("RETIRE %08x %08x %02x %08x",rpc,ri,rd,rw);
 end
 if(halted===1'b1) begin
  $display("STOP %01x %0d",trap,cycles);
  for(k=1024;k<1088;k=k+1) $display("MEM %08x %08x",k*4,mem[k]);
  $finish;
 end
 if(cycles>limit) $fatal(1,"watchdog timeout");
end
endmodule
