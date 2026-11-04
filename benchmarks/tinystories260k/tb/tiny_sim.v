module tiny_sim(input wire clk, input wire rx, input wire miso,
 output wire tx, output wire sclk, output wire cs, output wire mosi,
 output wire trap, output wire fault, output wire overrun, output wire [31:0] exitcode);
 wire wp,hold_n;
 tinystories_soc dut(clk,rx,tx,miso,mosi,sclk,cs,wp,hold_n);
 assign trap=dut.trap;
 assign fault=dut.bus_fault;
 assign overrun=dut.rx_overrun;
 assign exitcode=dut.debug_exit;
endmodule
