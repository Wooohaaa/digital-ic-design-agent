# HX1P35A 心跳输出

## 接口

- `I_sys_clk`: 50 MHz 系统时钟（约束 pin C4）。
- `I_rst_n`: 低有效复位（约束 pin R10）。
- `O_screen_pwm`: 心跳输出（约束 pin R11）。

## 行为

复位期间输出为 0。复位释放后，每计满 `HALF_PERIOD_CYCLES` 个时钟，输出翻转一次。默认参数为 25,000,000，因此输出频率约 1 Hz。testbench 使用短参数并检查复位、计数边界、输出翻转和稳定性。

## 板级说明

R11 在参考工程中命名为 `O_screen_pwm`，可能连接屏幕/背光 PWM。上板前需对照 HX1P35A 的实际 revision 与官方原理图确认连接和极性。
