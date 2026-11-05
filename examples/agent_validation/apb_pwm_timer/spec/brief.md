# APB PWM and timer validation task

Implement a compact APB3 slave containing a programmable-period PWM timer and sticky interrupt status. Preserve the module and port list in `rtl/apb_pwm_timer.v`. The host has authored a fixed APB master, cycle model, PWM checker, and pinned testbench.

The agent owns only `rtl/apb_pwm_timer.v`; the testbench and acceptance plan are host-owned.
