`timescale 1ns/1ps
module tb_io;
 reg clk=0; always #10 clk=~clk;
 reg rst_n=0, req=0; reg [23:0] addr=0;
 wire ready,sclk,cs,mosi; wire [31:0] data; reg miso=0;
 luna_spi_reader spi(clk,rst_n,req,addr,ready,data,sclk,cs,mosi,miso);
 integer bits=0, transactions=0; reg [31:0] command=0;
 function [7:0] flash_byte(input [23:0] a);
  flash_byte=(a*37)^(a>>4);
 endfunction
 always @(negedge cs) begin bits=0; command=0; transactions=transactions+1; end
 always @(posedge sclk) if(!cs) begin
  if(bits<32) command={command[30:0],mosi};
  bits=bits+1;
 end
 reg [23:0] byte_address; reg [7:0] b;
 always @(negedge sclk) if(!cs && bits>=32) begin
  if(command[31:24]!==8'h03) $fatal(1,"SPI opcode %h",command);
  byte_address=command[23:0]+((bits-32)/8);
  b=flash_byte(byte_address); miso=b[7-((bits-32)%8)];
 end
 task read_word(input [23:0] a);
  reg [31:0] expected; integer n, start_count;
  begin
   @(negedge clk); addr=a; req=1; start_count=transactions;
   n=0;
   while(!ready && n<600) begin @(negedge clk); n=n+1; end
   if(!ready) $fatal(1,"SPI watchdog: no ready after %0d cycles",n);
   expected={flash_byte(a+3),flash_byte(a+2),flash_byte(a+1),flash_byte(a)};
   if(data!==expected) $fatal(1,"SPI little endian got %h expected %h",data,expected);
   if(bits!=64 || !cs || sclk) $fatal(1,"SPI framing bits=%0d cs=%b clk=%b",bits,cs,sclk);
   repeat(10) begin @(negedge clk); if(ready) $fatal(1,"duplicate ready"); end
   if(transactions!=start_count+1) $fatal(1,"duplicate SPI request");
   req=0; repeat(4) @(negedge clk);
  end
 endtask
 reg tx_valid=0; reg [7:0] tx_data=0; wire tx_ready,tx,rx_valid; wire [7:0] rx_data;
 luna_uart #(.CLK_HZ(1600000),.BAUD(100000)) uart(clk,rst_n,tx_valid,tx_data,tx_ready,tx,tx,rx_valid,rx_data);
 integer seen=0; reg [7:0] expected_uart=0;
 always @(negedge clk) if(rx_valid) begin
  if(rx_data!==expected_uart) $fatal(1,"UART got %h expected %h",rx_data,expected_uart);
  seen=seen+1;
 end
 task loop_byte(input [7:0] value);
  integer before_count,n;
  begin
   while(!tx_ready) @(negedge clk);
   expected_uart=value; before_count=seen; tx_data=value; tx_valid=1;
   @(negedge clk); tx_valid=0;
   n=0; while(seen==before_count && n<200) begin @(negedge clk); n=n+1; end
   if(seen!=before_count+1) $fatal(1,"UART watchdog");
   while(!tx_ready) @(negedge clk);
   repeat(5) @(negedge clk);
  end
 endtask
 initial begin
  repeat(5) @(negedge clk); rst_n=1;
  read_word(24'h123456); read_word(0); read_word(24'hfffffc);
  $display("CHECK REQ-001 PASS");
  addr=32; req=1; repeat(16) @(negedge clk); rst_n=0;
  repeat(3) @(negedge clk);
  if(!cs || sclk || ready) $fatal(1,"SPI reset");
  req=0; rst_n=1; repeat(4) @(negedge clk); read_word(4092);
  $display("CHECK REQ-002 PASS");
  loop_byte(0); loop_byte(255); loop_byte(85); loop_byte(170);
  $display("CHECK REQ-003 PASS");
  $display("PASS IO protocol and reset"); $finish;
 end
 initial begin #1000000; $fatal(1,"global watchdog"); end
endmodule
