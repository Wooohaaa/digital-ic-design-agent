# APB PWM timer interface and behavior

Module: `apb_pwm_timer`.

Ports: `pclk`, synchronous active-low `presetn`; APB3 `psel`, `penable`, `pwrite`, `paddr[7:0]`, `pwdata[31:0]`, `prdata[31:0]`, `pready`, `pslverr`; outputs `pwm_out`, `irq`.

PREADY is always high. A transfer occurs only on a rising `pclk` edge when `psel && penable && pready`. Writes must not occur during setup (`psel && !penable`). Invalid addresses return PSLVERR during access and have no side effects. Valid register addresses are 0x00 PERIOD, 0x04 DUTY, 0x08 CONTROL, and 0x0C STATUS. PERIOD resets to 8 and writes clamp values below 2 up to 2. DUTY resets to 4 and stores the written value. CONTROL resets to 0; bit 0 enables the timer/PWM and bit 1 enables IRQ output. STATUS bit 0 is sticky pending and is cleared by writing one to bit 0 (W1C).

The counter resets to zero. When enabled, it increments each rising edge; when it reaches or exceeds PERIOD-1 it wraps to zero and sets STATUS pending. PWM is low while disabled and otherwise is high exactly when `counter < DUTY`. Thus DUTY=0 is always low, DUTY=PERIOD is always high for a stable period, and values above PERIOD also compare as high. IRQ is `CONTROL[1] && STATUS[0]`. Clearing pending deasserts IRQ unless a new wrap sets it on the same edge; set has priority over clear. Reads return the current register values in the APB access phase. No pins are mapped in this synthesis-only fixture.
