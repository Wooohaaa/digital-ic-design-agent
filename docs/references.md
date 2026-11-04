# 参考工程和技术来源

- [RISC-V RV32I 2.1 官方规范](https://docs.riscv.org/reference/isa/v20260120/unpriv/rv32.html)：本轮独立参考模型和 CPU 执行契约的指令语义来源。没有从候选 RTL 导出 golden 模型。
- [GPT-6 Luna 官方模型页](https://developers.openai.com/api/docs/models/gpt-6-luna)：独立 API provider 使用 Responses API；本轮实际模型运行来自当前会话 Luna 子代理，通过 exchange 文件响应接入。

- [RSI-Labs/RSI-Chip](https://github.com/RSI-Labs/RSI-Chip)：参考它把 RTL 与独立验证环境配对、用 golden model/directed cases 做功能比对，并在发布门禁中加入 LEC/实现检查的做法。该仓库当前是 API-first Rust EDA/CPU 项目，scope 远大于本 FPGA agent；此处只借鉴验证与可审计 gate，不复制其代码。
- [YosysHQ/PicoRV32](https://github.com/YosysHQ/picorv32)：小型、可综合 Verilog CPU，仓库带 Icarus testbench 和可直接运行的 `make test`；参考它把仿真放进普通开发循环的做法。该仓库已归档，适合作为 RTL/验证样例，不作为活跃依赖。
- [lowRISC/Ibex](https://github.com/lowRISC/ibex)：生产级 SystemVerilog RISC-V core，仓库分开维护 RTL、DV、formal、lint 和 synthesis；参考其按配置记录验证成熟度、把 verification gate 作为设计状态一部分的做法。它的 UVM/cosim 工具需求高于当前 HX1P35A MVP。
- [Anlogic 官方 TD Tcl 命令指南 SWUG105](https://tech.anlogic.com)：本机安装随附 `doc/SWUG/SWUG105_TCL_Command_User_Guide.pdf`。agent 的 TD 流程按官方工程 Tcl 方式创建工程、添加 RTL/约束、launch/wait synthesis 与 physical runs。
- [Anlogic 官方 TD 仿真 FAQ](https://tech.anlogic.com/Cn/Index/index/cate/32/tp/1/p/2.html)：TD 提供器件行为模型，但需第三方仿真器；agent 的默认功能验证使用 Icarus Verilog。
- [Aurora-520/anlogic-ph1p35-vision HX1P35A pin.adc](https://github.com/Aurora-520/anlogic-ph1p35-vision/blob/main/user_source/constraints_source/pin.adc)：当前样例只参考其中系统时钟 C4、复位 R10、screen PWM R11 的映射。该信息来自社区工程，实际上板前需以 HX1P35A 对应 revision 的官方原理图确认。
- [Anlogic PH1P35 Vision README](https://github.com/Aurora-520/anlogic-ph1p35-vision)：核对了目标器件 `PH1P35MDG324`、板卡名 HX1P35A 和 TD 6.2.x 工程使用方式。

## 仿真器选择

TD 官方 FAQ 说明 TD 不包含内置仿真工具；TD 的行为级和后布局仿真依赖 ModelSim/Questa 等外部工具。此 agent 选 Icarus Verilog 作为不依赖 TD license 的自检 RTL 仿真器。若工程实例化了 Anlogic 专用 primitive/IP，则需额外编译其仿真模型，并使用 TD 集成的第三方仿真流程；当前模板不含该类 IP。
