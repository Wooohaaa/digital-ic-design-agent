# HX1P35A HDMI rotating cube

This project renders a rotating 3-D wireframe cube to the HX1P35A HDMI connector using 640x480 DVI-compatible TMDS video. Geometry rotates on both axes; two on-chip one-bit buffers keep the displayed frame stable while the back buffer is cleared and redrawn.

## Board I/O

The HDMI data and forwarded-clock differential pairs use the board's HR bank 32 TMDS outputs:

| Signal | FPGA pins |
| --- | --- |
| TMDS data lane 0 (blue) | G16 / H16 |
| TMDS data lane 1 (green) | F17 / E18 |
| TMDS data lane 2 (red) | H14 / H13 |
| TMDS clock | C17 / D18 |
| 50 MHz reference clock | C4 |
| Board HDMI control / 5 V switch enable | B17 |

The board schematic connects B17 (`HDMI1_HPD`) to the TPS2051B enable for HDMI +5 V. `O_hdmi_hpd` is therefore driven high after the power-on delay to enable source-mode +5 V; it is not used as a cable HPD input. DDC is left unconnected.

## RTL simulation preview

The image below contains three actual 640×480 framebuffer captures generated from the final RTL at different rotation angles. It is a simulation preview, not a photograph or visual verification of the physical board.

![RTL simulation at angles 0, 16, and 32](reports/rtl-preview.png)

## Clocks and alignment

The PH1P35 uses `PH1P_PHY_PLL_V2` with `REFCLK_DIV=5`, `FBCLK_DIV=63`, and output dividers 25 and 5. This yields a 630 MHz VCO, a 25.2 MHz pixel clock, and a 126 MHz serializer clock; the clocks share the 50 MHz reference and their 5:1 relationship is constrained in SDC. The serializer sends each TMDS symbol least-significant bit first over DDRX5 and uses native differential output buffers.

The framebuffer read is synchronous and contributes one pixel of latency. The DE, HSYNC, and VSYNC control inputs are delayed by one pixel before the single-register TMDS encoders, aligning control symbols with the sampled framebuffer pixel. Blue carries HSYNC/VSYNC during blanking; red and green use control code 00.

## Acceptance result

The host-owned independent acceptance passed on 2026-11-05. All functional checks passed, including the complete top-level video stream. Native TD full produced a PH1P35MDG324 bitstream with setup WNS +7.286 ns, hold WNS +0.073 ns, setup/hold TNS 0 ns, 99.88% STA coverage, and 38/108 ERAM used. The implementation used 832/21,216 LUT6 and 4/40 DSP.

The initial TD run rejected `PH1P_PHY_PLL` as unsupported for this device/package. The installed PH1P device template and PLL support data identify `PH1P_PHY_PLL_V2` for PH1P35MDG324; after changing the primitive and keeping the divider configuration, the complete simulation and TD acceptance passed. This result establishes the current simulation and implementation gates only. Programming the FPGA and observing a connected HDMI display remain outstanding.

## Checks

Supplemental Icarus testbench commands are listed in `tb/README.md`. The operator-owned `verification/acceptance.py` performs the independent HDMI-cube acceptance, including native TD checks. Physical programming and display verification are performed by the host and are not implied by these sources.
