create_clock -name sys_clk_50m -period 20.000 [get_ports {I_sys_clk}]
create_generated_clock -name hdmi_pixel_clk -source [get_ports {I_sys_clk}] -master_clock [get_clocks {sys_clk_50m}] -divide_by 2 [get_pins {u_hdmi_clocks/u_hdmi_pll.clkc[4]}]
create_generated_clock -name hdmi_serial_clk -source [get_ports {I_sys_clk}] -master_clock [get_clocks {sys_clk_50m}] -multiply_by 2.5 [get_pins {u_hdmi_clocks/u_hdmi_pll.clkc[5]}]
# This is an asynchronous reset crossing; exclude setup/recovery only through its request net.
set_false_path -setup -from [get_clocks {sys_clk_50m}] -through [get_nets {pixel_reset_request}]
