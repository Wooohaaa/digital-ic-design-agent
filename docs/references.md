# 参考工程和技术来源

- [RISC-V RV32I 2.1 官方规范](https://docs.riscv.org/reference/isa/v20260120/unpriv/rv32.html)：本轮独立参考模型和 CPU 执行契约的指令语义来源。没有从候选 RTL 导出 golden 模型。
- [GPT-6 Luna 官方模型页](https://developers.openai.com/api/docs/models/gpt-6-luna)：独立 API provider 使用 Responses API；本轮实际模型运行来自当前会话 Luna 子代理，通过 exchange 文件响应接入。

- [RSI-Labs/RSI-Chip](https://github.com/RSI-Labs/RSI-Chip)：参考它把 RTL 与独立验证环境配对、用 golden model/directed cases 做功能比对，并在发布门禁中加入 LEC/实现检查的做法。该仓库当前是 API-first Rust EDA/CPU 项目，scope 远大于本 FPGA agent；此处只借鉴验证与可审计 gate，不复制其代码。
- [YosysHQ/PicoRV32](https://github.com/YosysHQ/picorv32)：小型、可综合 Verilog CPU，仓库带 Icarus testbench 和可直接运行的 `make test`；参考它把仿真放进普通开发循环的做法。该仓库已归档，适合作为 RTL/验证样例，不作为活跃依赖。
- [lowRISC/Ibex](https://github.com/lowRISC/ibex)：生产级 SystemVerilog RISC-V core，仓库分开维护 RTL、DV、formal、lint 和 synthesis；参考其按配置记录验证成熟度、把 verification gate 作为设计状态一部分的做法。它的 UVM/cosim 工具需求高于当前 HX1P35A MVP。
- [Anlogic 官方 TD Tcl 命令指南 SWUG105](https://tech.anlogic.com)：本机安装随附 `doc/SWUG/SWUG105_TCL_Command_User_Guide.pdf`。agent 的 TD 流程按官方工程 Tcl 方式创建工程、添加 RTL/约束、launch/wait synthesis 与 physical runs。
- [YosysHQ/yowasp](https://github.com/YosysHQ/yowasp) 与 [Yosys 文档](https://yosyshq.readthedocs.io/)：`icforge lec`/`icforge formal`（等价、`sat`、`clk2fflogic`、`chformal`、`memory`、`setundef`）、`icforge synth`（`techmap`、`stat -json`、`write_verilog`）与 `icforge implement --target generic`（同一 Yosys 后端）的可移植后端与命令语义来源。`icforge implement --target lattice-ice40-hx1k` 的 `synth_ice40` 也来自 Yosys。只调用其公开命令，不自带或移植其代码。
- [Verilator `--coverage`](https://verilator.org/guide/latest/exe_verilator.html#coverage) 与 [SystemC 覆盖记录格式](https://verilator.org/guide/latest/exe_verilator.html)：`icforge coverage` 用 Verilator `--binary --coverage` 构建自检 testbench，解析其写出的 `coverage.dat` 得到 line/branch/toggle/expr **结构**覆盖；加上 `--functional-coverage` 时还收集 `cover property`（类型 `user`、对象 `cover|S<line>`）的功能覆盖点。只调用其公开命令与数据格式，不自带或移植其代码；结构覆盖不等于功能覆盖，功能点命中不等于断言或规格覆盖，细节见 [coverage.md](coverage.md)。
- [SystemVerilog LRM `assert property` / `cover property`（IEEE 1800-2017 第 16 章）](https://standards.ieee.org/ieee/1800-2017/10492/)：`icforge coverage --functional-coverage` 检测 `assert property` 违例与 `cover property` 点命中的语义来源。本机实测 Verilator 5.048 支持模块级并发断言（违例非零退出 + `%Error: ... Assertion failed`）且在时钟边沿上**后 NBA 采样**，因此功能覆盖夹具的断言采用与 enable/count 采样顺序无关的复位行为；Icarus 不支持可移植的并发断言，细节见 [coverage.md](coverage.md)。
- [YosysHQ nextpnr（ice40）](https://github.com/YosysHQ/nextpnr) 与 [YosysHQ icepack](https://github.com/YosysHQ/icepack)：`icforge implement --target lattice-ice40-hx1k` 的开放布局布线与位流打包工具的语义来源，通过 portable `yowasp-nextpnr-ice40` / `yowasp-icepack` 调用，不自带或移植其代码。`icforge timing` 汇总的静态时序签核（`Max frequency for clock ... (PASS|FAIL at ... MHz)`）来自 nextpnr-ice40 的输出；post-route SDF 仿真需要 `icesim`，细节见 [timing.md](timing.md)。
- [Icarus Verilog](https://steveicarus.github.io/iverilog/)、[Verilator 文档](https://verilator.org/guide/latest/) 与 [Yosys `read_verilog`](https://yosyshq.readthedocs.io/en/latest/command/read_verilog.html)：SystemVerilog 支持范围的实测依据。`icforge verify`/`lint`/`synth`/`formal` 分别用 Icarus `-g2012`、Verilator `--lint-only -Wall` 和 Yosys `read_verilog -sv` 解析 SystemVerilog，支持矩阵见 [systemverilog.md](systemverilog.md)。
- [Verilator `--binary`](https://verilator.org/guide/latest/exe_verilator.html#verilating-and-building) 与 [UVM 方法学](https://www.accellera.org/downloads/standards/uvm)：`icforge svsim` 用 Verilator `--binary --timing` 构建并运行最小 class 化环境（事务/driver/scoreboard）。只借鉴 UVM 的组件划分，不自带或移植 UVM 库，细节见 [svsim.md](svsim.md)。
- [Verilator `--coverage`](https://verilator.org/guide/latest/exe_verilator.html#coverage) 与 [SystemC 覆盖记录格式](https://verilator.org/guide/latest/exe_verilator.html)：`icforge coverage` 用 Verilator `--binary --coverage` 构建自检 testbench，解析其写出的 `coverage.dat` 得到 line/branch/toggle/expr **结构**覆盖。只调用其公开命令与数据格式，不自带或移植其代码；结构覆盖不等于功能覆盖，细节见 [coverage.md](coverage.md)。
- [Anlogic 官方 TD 仿真 FAQ](https://tech.anlogic.com/Cn/Index/index/cate/32/tp/1/p/2.html)：TD 提供器件行为模型，但需第三方仿真器；agent 的默认功能验证使用 Icarus Verilog。
- [Aurora-520/anlogic-ph1p35-vision HX1P35A pin.adc](https://github.com/Aurora-520/anlogic-ph1p35-vision/blob/main/user_source/constraints_source/pin.adc)：当前样例只参考其中系统时钟 C4、复位 R10、screen PWM R11 的映射。该信息来自社区工程，实际上板前需以 HX1P35A 对应 revision 的官方原理图确认。
- [Anlogic PH1P35 Vision README](https://github.com/Aurora-520/anlogic-ph1p35-vision)：核对了目标器件 `PH1P35MDG324`、板卡名 HX1P35A 和 TD 6.2.x 工程使用方式。

## 仿真器选择

TD 官方 FAQ 说明 TD 不包含内置仿真工具；TD 的行为级和后布局仿真依赖 ModelSim/Questa 等外部工具。此 agent 选 Icarus Verilog 作为不依赖 TD license 的自检 RTL 仿真器。若工程实例化了 Anlogic 专用 primitive/IP，则需额外编译其仿真模型，并使用 TD 集成的第三方仿真流程；当前模板不含该类 IP。
