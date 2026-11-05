# HX1P35A rotating 3-D cube: operator contract

This is a real multi-Codex gpt-6-luna exercise. Deliver a synthesizable, resource-conscious
wireframe rotating 3-D cube over the board's directly connected HDMI connector (DVI-compatible
TMDS video, no audio/HDCP). Do not replace 3-D rotation with a fixed image or a rotating square.
No external DDR, CPU, framebuffer preload file, or fabricated hardware evidence.

## Board / primary sources

Anlogic PH1P35MDG324, TD family `ph1`, 50 MHz clock C4 LVCMOS33.
HDL2604 board manual pages 14/15 and supplied schematic HX1P35B_FT_40P_7.pdf:
data0 P=G16 N=H16; data1 P=F17 N=E18; data2 P=H14 N=H13;
clock P=C17 N=D18; HDMI_HPD/control B17; DDC SCL=E15 SDA=F15.
Use native TMDS33 differential output buffers in HR bank32. Do not drive differential
pairs using unrelated fabric pins or LVCMOS. Board B17 also controls TPS2051B's 5V enable;
inspect the schematic and document a source-mode high output. DDC may remain unconnected.

Primary vendor library: `C:/Anlogic/TD_6.2.1_Engineer_6.2.168.116/doc/UG1310_PH1P_Libraries_Guide_for_HDL_Designs.pdf`.
Relevant extracted text at original repository `reports/hdmi-board-docs/vendor-library.txt`.
Vendor simulator sources under `C:/Anlogic/TD_6.2.1_Engineer_6.2.168.116/sim_release/ph1p/`.
PH1P_LOGIC_HR_OSERDES has sclk,pclk,rst,idata[9:0],odata; ODDRMODE="DDRX5" is supported.
PH1P_LOGIC_OBUF_DIFF has i,t,o_p,o_n. Set t=0. PLL ports/parameters must be verified
against PH1P_PHY_PLL in the installed vendor simulator, not guessed from other Anlogic families.

## Fixed module contracts (Verilog/SystemVerilog synthesizable by TD)

1. `rtl/video_timing.v`: module video_timing(input clk, input reset, output [9:0] x,
   output [9:0] y, output de, output hsync, output vsync, output frame_start).
   Active-high synchronous reset. Counters restart at 0,0. Combinational outputs from counters.
   Horizontal active/front/sync/back=640/16/96/48, total800. Vertical=480/10/2/33,total525.
   de iff x<640 && y<480. Negative hsync for x in[656,752), vsync for y in[490,492).
   frame_start iff x==0 && y==480 (first vertical blank pixel), one cycle per frame.

2. `rtl/tmds_encoder.v`: module tmds_encoder(input clk, input reset, input [7:0] data,
   input de, input c0,input c1, output reg [9:0] symbol).
   Active-high synchronous reset. Standard DVI 8b-to-10b TMDS including signed running
   disparity, XOR/XNOR transition minimization, reset disparity in blanking. Exactly one
   registered encoder stage. Controls {c1,c0}=00:1101010100,01:0010101011,
   10:0101010100,11:1010101011. Bit0 serializes first. No look-up cheat limited to a palette.

3. `rtl/cube_vertices.v`: module cube_vertices(input [7:0] angle,
   output [79:0] vertices_x, output [79:0] vertices_y). Combinational geometry.
   Vertex i bits0/1/2 choose positive X/Y/Z; unset means negative. Coordinate magnitude64.
   Rotate about Y by a=2*pi*angle/256, then X by b=2*pi*((2*angle+32)%256)/256.
   sin/cos fixed-point quantization round(256*sin/cos), signed, including +/-256.
   xr=(X*cos(a)+Z*sin(a)) arithmetic>>>8; zr=(-X*sin(a)+Z*cos(a))>>>8;
   yr=(Y*cos(b)-zr*sin(b))>>>8; screen_x=320+((xr*3)>>>1), screen_y=240-((yr*3)>>>1).
   Pack unsigned10-bit screen positions for vertex i in bits i*10 +:10. All positions in frame.
   Use synthesizable quarter-wave/case ROM, explicit signed widths; no $sin/$cos/real in hardware.

4. `rtl/cube_framebuffer.v`: module cube_framebuffer(input clk,input reset,input frame_start,
   input [9:0] x,input [9:0] y,input [79:0] vertices_x,input [79:0] vertices_y,
   output reg [7:0] angle,output reg pixel_on,output busy).
   Two on-chip one-bit 640x480 buffers. Synchronous read => pixel_on corresponds to x/y
   sampled on the just-completed rising edge (one cycle), blank/outside address =>0.
   Never reset a memory with a huge loop; clear back-buffer sequentially. Clear 307200 pixels,
   capture all8 vertices coherently for current angle, rasterize all12 edges with integer
   Bresenham (inclusive endpoints, all octants): pairs differ in exactly one vertex-index bit.
   Frame0 uses angle0. Only swap completed back buffer on frame_start. Front is black until
   first valid swap. After swap advance angle by1 and render next frame; stable front between
   swaps; no read/write collision on displayed bank. All rendering must complete in <420000
   pixel cycles (one refresh period). Use inferred ERAM with synchronous reads, not flip-flop RAM.

5. `rtl/hdmi_phy.v`: modules hdmi_clocks(input clk50,input reset,
   output clk_pixel,output clk_serial,output locked) and hdmi_serializer(input clk_pixel,
   input clk_serial,input reset,input [9:0] blue,input [9:0] green,input [9:0] red,
   output [2:0] data_p,output [2:0] data_n,output clock_p,output clock_n).
   Hardware pixel clock25.2MHz (640x480 exactly60Hz), serial clock126MHz DDR (252Mb/s per lane).
   Prefer PLL VCO630MHz with legal vendor dividers, but derive actual legality/parameters from
   installed source. Use HR_OSERDES DDRX5 plus DIFF buffers; no fabric 252MHz bit-bang.
   Blue->lane0,green->lane1,red->lane2; bit0 first; clock forwarded differentially.
   Include clearly separated `ifdef ICFORGE_SIM behavioral models, timescale1ps/1ps;
   simulation half-periods pixel19840ps/serial3968ps exactly5:1. Behavioral model must preserve
   symbol boundaries and word latency, reset deterministically, and document its alignment.
   `ICFORGE_SIM must never be enabled in TD synthesis; no vendor primitive redefinitions.

6. `rtl/hdmi_cube_top.v`: module hdmi_cube_top(input I_sys_clk,output [2:0] O_hdmi_data_p,
   output [2:0] O_hdmi_data_n,output O_hdmi_clk_p,output O_hdmi_clk_n,output O_hdmi_hpd).
   Self-starting POR counter initialized to0 from50MHz; reset while POR or !PLLlocked, release
   reset synchronously in pixel domain. Instantiate fixed modules, align de/hsync/vsync with
   framebuffer's registered read and encoder latency. Cube RGB=(40,220,255); black background.
   No blinking pause or tearing. Drive source enable correctly after schematic check.
   Add `project.json`, `constraints/hx1p35a.adc`, `constraints/hx1p35a.sdc` and README.
   SDC must constrain 50MHz input, all generated clocks25.2/126MHz and their relationship;
   do not blanket false-path pixel/serializer crossings or waive negative setup/hold slack.
   manifest board=hx1p35a, device_family=ph1, device=PH1P35MDG324, top=hdmi_cube_top.

## Verification and work assignment

Separate six independent jobs for timing, TMDS, geometry, framebuffer, PHY and supplemental
tests (`tb/` only). Then top/integration job depends on all implementation jobs and read-only
review depends on integration and tests. All executors must be codex, explicitly gpt-6-luna.
Use at most6 native Codex processes simultaneously, disjoint file scopes. Do not let child
workers spawn other agents. Preserve this SPEC and `verification/` (operator-owned acceptance).
No edits to other benchmark projects, IC Forge source/config, trust roots, tests outside your scope.

Independent operator acceptance checks timing for multiple complete rasters; exhaustive/random
TMDS control/data/disparity; all256 geometric poses against high-precision reference; buffering,
frame update and all12 edges; rendered frame images; serializer word decoding/reset; native TD
synthesis/P&R, setup/hold and exact pin constraints. Supplemental tests are useful but cannot
replace this acceptance. The operator's check entrypoint is `verification/acceptance.py`.
Only report checks actually executed. Runtime logs/build files are ignored and not imported.
Do not program hardware from child workers. Host handles a single exclusive JTAG deployment
after gates. No HDMI display is connected yet: physical display verification is pending.
