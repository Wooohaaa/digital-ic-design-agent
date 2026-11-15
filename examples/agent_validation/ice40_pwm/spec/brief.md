# PWM breathing-LED validation task (vendor-neutral iCE40 target)

Implement a synthesizable PWM "breathing" generator under `rtl/pwm_breath.v` for the open Lattice iCE40 target (`lattice-ice40-hx1k`, iCEstick / iCE40HX1K-TQ144).

The block is a free-running 8-bit counter that drives a duty-cycle comparison output and a coarse level nibble. It must pass the host self-checking testbench (`tb/tb_pwm_breath.v`) and then implement through the open Yosys + nextpnr-ice40 + icepack flow to a real bitstream.

The agent owns only `rtl/pwm_breath.v`. The host owns the fixed testbench, the pin constraints (`constraints/pins.pcf`) and the SHA-256 pin in `project.json`. This fixture pins the *vendor-neutral* path: no Anlogic TD and no license is required.
