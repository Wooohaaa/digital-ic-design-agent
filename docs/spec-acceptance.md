# 单时钟设计的规格验收

使用同一个入口验证两个现有设计。两槽 FIFO 对照有序列表，检查空满、顺序和复位优先级；8 位计数器对照两个四位计数段，检查高有效复位、使能为低时保持，以及 255 加一回绕为零。

```powershell
$env:PYTHONPATH = (Get-Location).Path
python scripts/run_spec_checks.py fifo --output reports/fifo-spec
python scripts/run_spec_checks.py counter --output reports/counter-spec
python scripts/run_spec_checks.py counter --output reports/counter-spec --verify
```

新证明使用空路径创建快照目录；`--verify` 检查现有报告的当前源码、契约、各项证明、日志和反例完整性，无需启动 Yosys。它是本地证据核对，重新执行 SAT 应指定新输出目录。

独立夹具可在 `scripts/run_spec_checks.py` 注册审核过的 `SpecContract`，工程则通过清单指定 JSON 契约。契约声明 DUT 顶层、独立参考、完整输入端口、复位极性、可选状态观察、可达目标和负例。参考须实例化 DUT 并提供标量 `relation_ok` 和 `ports_ok`。状态观察只增加输出端口；审核时确认参考未依赖 DUT 的组合决策。公共入口生成固定的证明脚本。

模型检查只允许一个直接连接到主输入 `clk` 的上升沿时钟、受支持的同步寄存器和组合单元。每个 SAT 查询把主输入和初始寄存器约束为已定义二值，不指定初始数值；基例只在第一拍约束复位有效。随后证明关系在任意一步保持，以及关系推出可观察输出正确。开始条件、任意输入保证、占用/回绕的可达见证以及复位后可达的缺陷反例分别入档。两设计各有三项负例；计数器饱和错误的反例要先走过 255 次使能，直到本应回绕的一拍。

验收范围是首次同步复位后的二值安全性质；不包含活性、异步复位、多时钟、四态或物理签核。规范证明保证 **RTL 符合参考行为**，而 [LEC](lec-state.md) 检查 **RTL 和综合网表是否等价**，二者独立。SAT 参数可查 [Yosys 文档](https://yosyshq.readthedocs.io/projects/yosys/en/latest/cmd/index_formal.html#sat-solve-a-sat-problem-in-the-circuit)。

## 工程门禁

`examples/agent_validation/sync_fifo` 是四槽、8 位数据、注册读输出的独立工程，和上述两槽 ready/valid FIFO 不同。其 `project.json` 通过 `formal_specification` 显式要求规格证明，并固定本地契约、独立参考的审核指纹；修改内容需要重新审核并重新固定指纹。审核指纹可兼容 Git 的 LF/CRLF，运行及归档指纹仍精确到字节。

`examples/lec/counter` 通过同一门禁运行非 FIFO 的第二个工程。它的顶层 `counter_demo` 实例化原 8 位计数器；仿真、规格和 LEC 都使用清单声明的两个 RTL 文件。证明检查契约列出的输入和参数默认值是否与 Yosys 展开后的 DUT 完整匹配，拒绝双向口。参考须直连唯一 DUT 的全部输入，并使用已核对的默认参数。计数器固定为高有效复位；FIFO 固定为 4 槽、8 位。多源支持自包含文件，暂不接收 include 依赖、外部参数覆盖或多时钟。

`examples/agent_validation/apb_wait_register` 使用 setup、一个等待周期和完成三个阶段。独立参考通过前两拍事件判断完成资格，对任意二值输入检查 ready、读数据及错误输出，并用写入计数检查每次合法写入仅提交一次。仿真还重放综合后的网表。取消、错误无副作用和非法继续保持 access 的行为由[外设契约](../examples/agent_validation/apb_wait_register/spec/design.md)定义。握手和等待周期参考 [Arm AMBA 3 APB 规范](https://documentation-service.arm.com/static/64257d8379edab6d64fb6f68)。

```powershell
python -m fpga_agent flow examples/agent_validation/sync_fifo --simulation-only
python -m fpga_agent flow examples/lec/counter --simulation-only
python -m fpga_agent lint examples/agent_validation/apb_wait_register
python -m fpga_agent flow examples/agent_validation/apb_wait_register --simulation-only
# 也可以逐步运行 spec-check、verify、lec 和 package --simulation-only。
python -m fpga_agent review examples/agent_validation/sync_fifo --scope simulation
```

`flow --simulation-only` 生成仿真证据，更新所需规格证明和 LEC，再交付源码与可复核证据包。若仅运行 `package` 或直接进入 TD，缺失、过期或失败的规格证明会阻止后续动作。工程审查用 `DV-001`、`LEC-001` 和 `FORMAL-001` 区分三项。规格报告、模型、脚本、日志、反例和参考写入 ZIP，并按指纹复核；同版本检查器可在解包后再验证。

三个试点均为仿真交付，板级引脚映射和物理签核待验证。
