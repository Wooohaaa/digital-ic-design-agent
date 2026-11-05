# HDMI cube supplemental testbenches

These benches exercise the fixed module contracts in `SPEC.md`; they are supplemental to, and do not replace, `verification/acceptance.py`.

Run from `benchmarks/hdmi_cube` with Icarus Verilog:

```sh
iverilog -g2012 -s tb_video_timing -o /tmp/timing.vvp rtl/video_timing.v tb/tb_video_timing.sv && vvp /tmp/timing.vvp
iverilog -g2012 -s tb_tmds_encoder -o /tmp/tmds.vvp rtl/tmds_encoder.v tb/tb_tmds_encoder.sv && vvp /tmp/tmds.vvp
iverilog -g2012 -s tb_cube_vertices -o /tmp/vertices.vvp rtl/cube_vertices.v tb/tb_cube_vertices.sv && vvp /tmp/vertices.vvp
iverilog -g2012 -s tb_cube_framebuffer -o /tmp/framebuffer.vvp rtl/cube_vertices.v rtl/cube_framebuffer.v tb/tb_cube_framebuffer.sv && vvp /tmp/framebuffer.vvp
iverilog -g2012 -DICFORGE_SIM -s tb_hdmi_phy -o /tmp/phy.vvp rtl/hdmi_phy.v tb/tb_hdmi_phy.sv && vvp /tmp/phy.vvp
```

The PHY bench checks the specified behavioral clock periods, differential outputs, lane mapping, bit order, and five consecutive changing symbols while tolerating fixed serializer latency and arbitrary starting bit phase.
