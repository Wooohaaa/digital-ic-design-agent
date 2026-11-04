`timescale 1ns/1ps
// Integration harness: all Transformer compute executes in the Luna RV32I core.
module tinystories_soc(
 input wire I_sys_clk, input wire I_uart_rx, output wire O_uart_tx,
 input wire I_flash_miso, output wire O_flash_mosi, output wire O_flash_clk,
 output wire O_flash_cs_n, output wire O_flash_wp_n, output wire O_flash_hold_n
);
 // Capture returning SPI data on the opposite 50MHz edge.
 // This gives a physical half-cycle hold margin without relaxing IO constraints.
 reg flash_miso_sample;
 always @(negedge I_sys_clk) flash_miso_sample<=I_flash_miso;
 reg [7:0] power_on=0;
 always @(posedge I_sys_clk) if(!(&power_on)) power_on<=power_on+1'b1;
 wire rst_n=&power_on;
 wire iv,ir, dv,dr, halted,trap,retire;
 wire [31:0] ia,da,dw; wire [3:0] strb;
 reg [31:0] rom_data,ram_data,other_data=0; reg [1:0] read_region=0;
 wire [31:0] bus_read=read_region==0?rom_data:read_region==1?ram_data:other_data;
 reg bus_ready=0;
 wire [31:0] retire_pc,retire_insn,retire_data; wire [4:0] retire_rd;
 assign ir=bus_ready&&iv; assign dr=bus_ready&&dv;
 luna_rv32 cpu(.clk(I_sys_clk),.rst_n(rst_n),.imem_addr(ia),.imem_valid(iv),.imem_ready(ir),
  .imem_rdata(bus_read),.dmem_valid(dv),.dmem_ready(dr),.dmem_wstrb(strb),.dmem_addr(da),
  .dmem_wdata(dw),.dmem_rdata(bus_read),.retire_valid(retire),.retire_pc(retire_pc),
  .retire_insn(retire_insn),.retire_rd(retire_rd),.retire_wdata(retire_data),.halted(halted),.trap(trap));
 wire valid=iv||dv;
 wire [31:0] address=dv?da:ia;
 wire write=dv&&(|strb);
 (* rom_style="bram" *) reg [31:0] rom[0:8191];
 (* ram_style="bram" *) reg [31:0] ram[0:32767];
 initial $readmemh("firmware/rom.hex",rom);
 reg tx_valid=0; reg [7:0] tx_data=0; wire tx_ready,rx_valid; wire [7:0] rx_data;
 luna_uart uart(.clk(I_sys_clk),.rst_n(rst_n),.tx_valid(tx_valid),.tx_data(tx_data),
  .tx_ready(tx_ready),.tx(O_uart_tx),.rx(I_uart_rx),.rx_valid(rx_valid),.rx_data(rx_data));
 reg rx_pending=0,rx_overrun=0; reg [7:0] received=0;
 reg [3:0] spi_gpio=1;
 wire spi_clk,spi_cs,spi_mosi,spi_ready; wire [31:0] spi_data;
 localparam [1:0] IDLE=0, ACK=1, FLASH=2;
 reg [1:0] state=IDLE; reg [23:0] flash_addr=0;
 reg bus_fault=0; reg [31:0] debug_exit=0;
 luna_spi_reader flash(.clk(I_sys_clk),.rst_n(rst_n),.req(state==FLASH),.req_addr(flash_addr),
  .ready(spi_ready),.rdata(spi_data),.sclk(spi_clk),.cs_n(spi_cs),.mosi(spi_mosi),.miso(flash_miso_sample));
 assign O_flash_cs_n=spi_gpio[3]?spi_gpio[0]:spi_cs;
 assign O_flash_clk=spi_gpio[3]?spi_gpio[1]:spi_clk;
 assign O_flash_mosi=spi_gpio[3]?spi_gpio[2]:spi_mosi;
 assign O_flash_wp_n=1'b1;
 assign O_flash_hold_n=1'b1;
 integer lane;
 // Dedicated synchronous read registers let TD infer ERAM instead of
 // distributing the entire 128KiB store across logic slices.
 always @(posedge I_sys_clk) begin
  if(rst_n && state==IDLE && valid && address<32'h00008000)
   rom_data<=rom[address[14:2]];
  if(rst_n && state==IDLE && valid && address>=32'h20000000 && address<32'h20020000) begin
   ram_data<=ram[address[16:2]];
   if(write) for(lane=0;lane<4;lane=lane+1)
    if(strb[lane]) ram[address[16:2]][lane*8+:8]<=dw[lane*8+:8];
  end
 end
 always @(posedge I_sys_clk) begin
  bus_ready<=0; tx_valid<=0;
  if(!rst_n) begin
   state<=IDLE; bus_ready<=0; spi_gpio<=1; rx_pending<=0; rx_overrun<=0;
   bus_fault<=0; debug_exit<=0;
  end else begin
   case(state)
    IDLE: if(valid) begin
     if(address<32'h00008000) begin
      read_region<=0;
      if(write) bus_fault<=1;
      bus_ready<=1; state<=ACK;
     end else if(address>=32'h20000000 && address<32'h20020000) begin
      read_region<=1;
      bus_ready<=1; state<=ACK;
     end else if(address>=32'h10000000 && address<32'h11000000 && !write && !spi_gpio[3]) begin
      flash_addr<={address[23:2],2'b00}; state<=FLASH;
     end else if(address[31:4]==28'h4000000) begin
      read_region<=2;
      case(address[3:2])
       0: if(!write || tx_ready) begin
        other_data<={24'b0,received};
        if(write) begin tx_data<=dw[7:0]; tx_valid<=1; end
        else begin rx_pending<=0; rx_overrun<=0; end
        bus_ready<=1; state<=ACK;
       end
       1: begin other_data<={29'b0,rx_overrun,tx_ready,rx_pending}; bus_ready<=1; state<=ACK; end
       2: begin other_data<={31'b0,flash_miso_sample}; if(write) spi_gpio<=dw[3:0]; bus_ready<=1; state<=ACK; end
       3: begin other_data<=debug_exit; if(write) debug_exit<=dw; bus_ready<=1; state<=ACK; end
      endcase
     end else begin bus_fault<=1; other_data<=0; read_region<=2; bus_ready<=1; state<=ACK; end
    end
    ACK: state<=IDLE;
    FLASH: if(spi_ready) begin other_data<=spi_data; read_region<=2; bus_ready<=1; state<=ACK; end
    default: state<=IDLE;
   endcase
   // A new arriving byte wins over a simultaneous software pop.
   if(rx_valid) begin
    if(rx_pending && !(state==IDLE && valid && !write && address==32'h40000000)) rx_overrun<=1;
    received<=rx_data; rx_pending<=1;
   end
  end
 end
endmodule
