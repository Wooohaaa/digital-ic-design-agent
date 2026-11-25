# 官方 UVM 有界运行契约

2026-11-20：v0.14 加入 `SV-FIFO-RESET-PRIORITY`，共 **29 个用例、38 个有界特性**，其中官方 UVM 用例仍为 17 项。

新增用例直接驱动共享 FIFO RTL，不依赖 UVM。三种占用状态（空、占用一槽、满）× 四种输入 valid / 输出 ready 组合 × 有无复位，共 24 个场景。Python 队列模型核对 216 个边沿的采样前后端口值、清空、排空与恢复。占用一槽且两端同时请求时，无复位对照同拍出入队；复位时清空，两侧均不留下事务。满队列即使发生出队，也要等到下一拍才能接受写入。参见[网表重放](fifo-flow.md)。

## v0.13：两轮竞争复位

2026-11-20：v0.13 加入 `UVM-FIFO-REPEAT-RESET`，当时为 28 个用例、37 个有界特性，含 17 项官方 UVM。

同一个 FIFO 和 sequencer 经历两轮同步复位。每轮由一条持锁 sequence 写满 FIFO 并阻塞于第三个请求，另一条已请求但尚未获准；检查两条旧 sequence 均停止，锁和待授权请求清空。第二轮复位后，两条普通 sequence 复用事务 ID 80，scoreboard 只收到新数据 100、101。独立 Python 核对 26 个采样边沿、阻塞期间数据保持、每轮实际取消以及最终交付。两轮各只有一个持锁者和一个竞争者。

## v0.12：满队列同步复位

2026-11-20：v0.12 加入 `UVM-FIFO-SYNC-RESET`，当时为 27 个用例、36 个有界特性，官方 UVM 用例 16 项。

FIFO 已满、第三个写请求受阻时，接收端置 ready，随后在时钟沿之间断言复位。队首必须保留到上升沿，随后队列清空；复位边沿不发布旧数据，未被接收的写请求由 UVM 取消。复位后两笔新事务复用旧 ID，但分别交付新载荷 100、101。原始端口采样、取消记录、sequence 完成数和 scoreboard 由 Python 独立核对。同一夹具也用于[网表重放](fifo-flow.md)。

## v0.11：同源 FIFO 网表重放

2026-11-20：v0.11 将 `UVM-RTL-FIFO` 的 DUT 拆为独立编译单元，由契约的 `additional_sources` 声明。保留 26 个用例、35 个有界特性，其中 15 项使用固定的官方 UVM。编译与静态检查的源文件列表纳入证据重验。

## v0.10：RTL FIFO 与双向反压

2026-11-19：v0.10 新增 `UVM-RTL-FIFO`。历史仿真结果对应当时内嵌于测试平台的 FIFO DUT。

sequence 尝试六个事务，仅五个合法事务写入两槽 RTL FIFO。该夹具使用单时钟，空队列不直通，队满时输入暂停，直到出队后的下一拍才重新接受。driver 和接收端在下降沿驱动；monitor 在上升沿读原始端口，并只在出口握手时发布给 scoreboard。Python 用独立队列模型验证 16 拍，包括满时输入保持、输出反压时队首保持、同拍出入队、五笔按序交付和最后排空。验证使用随机载荷与固定反压计划；不外推到任意深度、途中复位、跨时钟或现有 `sync_fifo` 示例。

ModelSim 和 Verilator 的新项各观察到 3 拍输入阻塞、6 拍输出阻塞、2 次同拍出入队。Icarus 在编译官方 UVM 库时拒收，slang 接受；两种开源后端本轮只测新增项。单项复现：`python -m fpga_agent sv-check --backend modelsim --case UVM-RTL-FIFO --output reports/uvm-rtl-fifo.json`。现场数据和变异证据见根工作区 `validation/2026-11-19-uvm-v010-summary.json`。

## v0.9：随机事务经 UVM 交付

2026-11-19：v0.9 新增 `UVM-RANDOMIZED-ITEM`，当时为 25 个用例、33 个有界特性，其中 14 项使用固定的官方 UVM。

同一条 sequence 尝试四个事务：类约束、内联允许集合、无解内联约束、第二组内联允许集合。仅求解成功后才申请并发送事务。driver 从 sequencer 接收原始数据并逐值发布至 scoreboard；Python 检查每个求解结果的合法性、三个观察点的 ID 与字段完全一致、无解项保持初值且未进入 driver 或 scoreboard，以及无解后恢复正常交付。两个仿真器可选择不同合法解，不比较随机分布。

单项复现：`python -m fpga_agent sv-check --backend modelsim --case UVM-RANDOMIZED-ITEM --output reports/uvm-randomized-item.json`。换用 Verilator 时按 [运行边界与复现](verilator-runtime.md) 配置编译工具和求解器。新增变异分别强制下发无解项、破坏载荷、改变 ID、遗漏向 scoreboard 发布。

本机 ModelSim 通过全部 25 项，Verilator 通过新增项；Icarus 在编译官方 UVM 时拒收，独立 slang 前端接受。两种开源后端本轮只测新增项。逐后端结果见根工作区 `validation/2026-11-19-uvm-v09-summary.json`。

## v0.8：SV 约束随机化

2026-11-19：v0.8 新增不依赖 UVM 的 `SV-RANDOMIZE`，当时为 24 个用例、31 个有界特性，其中 13 项使用固定的官方 UVM。

Python 独立枚举两组四位字段的合法组合，核对 13 行原始数据。类约束要求左值在 2 至 9，右值为左值加 2；内联约束进一步取交集。禁用左字段后，固定为 5 时仍能求出右值 7，固定为 12 时必须失败并保留两个字段。重新启用后可解出 (3,5)；另一处显式矛盾也必须返回 0 并保留原值。检查不要求相同随机序列，也不评价分布、种子稳定性或复杂约束。

单项复现：`python -m fpga_agent sv-check --backend modelsim --case SV-RANDOMIZE --output reports/sv-randomize.json`。Verilator 同样可运行该用例，本轮使用 Z3 4.15.5，可通过 `VERILATOR_SOLVER` 指向求解器，见 [Verilator 安装说明](https://verilator.org/guide/latest/install.html#install-z3)。四项变异分别改错字段关系、改变内联集合、错误启用冻结字段以及把无解条件改为可解。

本机 ModelSim 通过全部 24 项，Verilator 通过新增项，Icarus 在编译新夹具时拒收 constraint 和 inside。后两者本轮只测新增项。28 项行为变异均被检出；证据见根工作区 `validation/2026-11-19-sv-v08-summary.json`。

## v0.7：RTL 异步复位与 UVM 驱动器

2026-11-19：v0.7 新增 `UVM-RTL-RESET`，当时为 23 个用例、28 个有界特性，其中 13 项使用固定的官方 UVM。

单槽 RTL 通过 ready/valid 接受请求并产生加 7 的响应。驱动器在旧响应因反压滞留时收到低有效复位：先在两个时钟上升沿之间观察 RTL 已清除响应，再在下一上升沿让驱动器取消旧请求并释放 sequencer 的锁。复位解除后，新 sequence 复用事务 ID 7，只完成新响应 97。

独立 Python 参考核对缓冲、异步清除、取消和恢复四行观测。三项变异分别把异步复位改成同步、给出错误的复位数据、在驱动器中把取消误记为完成。此夹具限于单时钟、首笔已被 RTL 接收且响应仍受反压的场景；未覆盖请求尚未接受时的取消、多在途事务或跨时钟复位。

本机 ModelSim 通过全部 23 项，Verilator 通过新增项，Icarus 在编译官方 UVM 库时拒收。后两者本轮只重测新增项。24 项行为变异全部检出，证据见根工作区 `validation/2026-11-19-uvm-v07-summary.json`。

## v0.6：sequencer 停止与重启

2026-11-18：v0.6 新增 `UVM-STOP-RESTART`，当时为 22 个用例、27 个有界特性，其中 12 项使用固定的官方 UVM。

探针先完成一笔请求并保留未读响应 111，再让驱动器持有第二笔未完成请求，另一个 sequence 被锁挡在队列中。调用 `stop_sequences()` 后两个 sequence 都进入 `UVM_STOPPED`，锁和可授予请求清空，第二笔事务不算完成。驱动器同时丢弃取消的请求句柄，避免事后调用 `item_done`。

停止后原对象仍有一笔未读响应。对同一个 sequence 实例再次调用 `start()` 时，响应队列在进入 body 前清空，即使复用事务 ID 7，也只读到新响应 121。用例分别观察停止和重启两个时点；未覆盖 RTL reset、驱动器自主处理中断或错误回报已取消事务。

新增两个行为变异：跳过重启时响应队列清理、在停止前错误完成本应取消的事务。两者均要求仿真正常结束后由独立参考拒收。

ModelSim 通过全部 22 项；Verilator 通过新探针，Icarus 在编译官方库时拒收。两种开源后端本轮仅重跑新探针。全部 21 项行为变异都被检出，证据见根工作区 `validation/2026-11-18-uvm-v06-summary.json`。

## v0.5：sequencer 独占与释放

2026-11-18：v0.5 新增 `UVM-LOCK-GRAB`，当时为 21 个用例、25 个有界特性，其中 11 项使用固定的官方 UVM。

原持有者先取得 `lock`，另外三个 sequence 随后分别请求普通事务、`lock` 和 `grab`。在原持有者发出第一笔事务前检查三个竞争者均被阻塞；驱动器每次接收事务时记录值和当前持有者身份。参考授予次序为 **11 → 12 → 31 → 21 → 41**：原持有者连续完成两笔，后来的 `grab` 在释放后越过等待中的 `lock`，普通请求最后恢复。末尾还检查显式释放与无人持锁。

四项轨迹变异分别将 `grab` 换成 `lock`、移除首次加锁、提前释放、延迟 `ungrab` 至释放检查之后。另一次诊断试验完全移除 `ungrab`，官方库发出 `SEQFINERR` 并在退出时清理，故不计入纯轨迹变异通过数。范围限于一个已持有者、一个等待 lock 和一个等待 grab，不覆盖嵌套持锁、多个 grab 的顺序或停止/复位。

从源码目录单独复现：`python -m fpga_agent sv-check --backend modelsim --case UVM-LOCK-GRAB --output reports/uvm-lock-grab.json`。如果改用 Verilator，则按 [构建工具说明](verilator-runtime.md) 配置环境，并给生成的 C++ 构建留出时间（例如 `--timeout 180`）。

完整 v0.5 矩阵中，ModelSim **21/21**、Verilator **19/21**、Icarus **8/21**。新探针在前两者通过；Verilator 仍未通过四态和 fatal 终止，Icarus 仍无法编译官方 UVM 库。最终 19 项行为变异全部检出；证据见根工作区 `validation/2026-11-18-uvm-v05-summary.json`。

## v0.4：配置范围与分阶段优先级

2026-11-18：v0.4 新增 `UVM-CONFIG-PRECEDENCE`，共有 **20 个用例、23 个有界特性**，其中 10 项使用固定的官方 UVM。官方库与 174 文件哈希锁保持不变。

新探针在两条 lane 的 `sink` 上匹配相对通配范围 `lane_*.sink`，同时读取不应匹配的 `tap`。在 build 阶段，测试层先写精确路径、再更新同层通配路径；随后子组件写入自己的精确路径。两条 `sink` 均应得到测试层最后写入的值 23，`tap` 应保留未找到的初值。run 阶段在叶节点、测试层和叶节点依次写入，目标读取依次为 41、53、61；另一条 lane 与 `tap` 保持原值。

独立 Python 参考核对 8 行观测。四项新增变异分别缩窄通配更新、改变 build 写入者的层级、调换同层写入顺序、将最后一次运行期更新写到另一条 lane。每项都必须正常结束，且由精确轨迹拒收。v0.4 仍不覆盖任意正则表达式、异步 `wait_modified`、跨类型资源冲突或所有层级组合。

本机 ModelSim 通过全部 20 项，Verilator 通过新增配置探针，Icarus 在该探针编译官方库时拒收。后两者仅重跑该探针。15 项行为变异均被检测，含全部四项新增配置变异；本轮结果保留在根工作区 `validation/2026-11-18-uvm-v04-summary.json`。

## v0.3：多 sequence 仲裁与响应

2026-11-18：v0.3 增加 `UVM-ARBITRATION`，当时为 19 个用例、21 个有界特性，其中 9 项使用固定的官方 UVM。

三个 sequence 在不同时刻入队，驱动器等三者均可发出请求后才取第一笔。独立 Python 参考要求高优先级先被授予、两个高优先级请求者按 FIFO 交替获准，低优先级最后获得服务。每个 sequence 发两笔请求，三个 sequence 共用事务 ID 1 和 2。

驱动器完成六笔请求后才以逆序返回结果。通过 `set_id_info` 保留原请求的 sequence/transaction ID，sequence 使用带事务 ID 的 `get_response`。19 行观测同时检查授予顺序、实际发送顺序、每个响应的归属、事务 ID 和数据。超时看门狗、UVM severity 总结与 DONE 条件沿用。

新增四项行为变异：改用普通 FIFO、移除响应的事务 ID 过滤、把响应转给错误的 sequence、篡改响应值。新变异必须正常编译运行并完成夹具，再由轨迹不匹配检出；看门狗结束不能算检测成功。变异脚本现按独立标签保存同一夹具的多种变异。

这一用例采用固定优先级和固定时序，尚不检验动态优先级、reset、响应溢出或 handler。后续独立 lock/grab 测试见本页开头。

本机 ModelSim 10.6e 通过 v0.3 的全部 19 项；Verilator 5.055 通过新增用例，Icarus 13.0 在新增用例中仍未编译通过官方库。后两者本轮只运行新增项；完整 v0.2 的 Verilator 四态及 fatal 失败仍见 [历史矩阵说明](verilator-runtime.md)。

## v0.2 历史记录

2026-11-17，第六轮，接续 [第五轮第二后端](sv-backends.md)。长期目标仍是 Agent 主导 EDA；这里完成的是固定官方库上的运行夹具与反例检测，未实现自研 UVM 或完整 SystemVerilog 仿真器。

契约 `rsi-sv-uvm-v0.2` 包含18个case、19个有界feature：本机 ModelSim 10.6e 全部通过，Icarus 基础8项通过但完整矩阵仍fail。官方库仍为 Accellera 2020.3.1 / commit `78c06547a2a0a29b3dc9dcafae62b75b2ff61544`，174文件哈希锁定、UVM_NO_DPI，未改官方库。

### v0.2 运行边界

| Case | 检查的实际结果 | 未覆盖的例子 |
| --- | --- | --- |
| UVM-COMPONENT | 两级父子关系、全名、直接/相对查找的对象身份、缺失子项 | 动态拓扑、复杂查找规则 |
| UVM-FACTORY | 注册、create、单个类型override、派生cast及虚函数、direct new不被override | instance override、override链及恢复 |
| UVM-CONFIG | 两个兄弟节点精确作用域、int set/get、缺失键返回false且不改输出值 | wildcard/优先级/资源竞争 |
| UVM-ANALYSIS | 一个port连接两个subscriber export，4笔事务的值、顺序及对象身份 | 异步scoreboard、完整TLM协议 |
| UVM-SEQUENCE | 单sequence/sequencer/driver的4笔延迟get_next_item/item_done，数据顺序与完成数 | 多sequence仲裁、响应、重置、锁 |
| UVM-REPORT | 两次info/一次warning的计数增量，以及零error/fatal | 完整verbosity、catcher、action策略 |
| UVM-FATAL | 指定ID/message的真实fatal、计数为1、零error及禁止继续执行 | 任意fatal变体或所有退出模式 |

连同此前 UVM-PHASE 的 build/run/report 顺序与 raise/drop objection，共8个UVM运行case、8个feature。独立Python参考持有精确预期，普通用例要求唯一DONE；UVM用例还必须有唯一的零error/fatal总结。只选择report正例时，report feature为partially_verified，因为fatal探针未运行。

fatal探针刻意不打印DONE。它要求实际 `RSI_EXPECTED_FATAL` 消息和severity总结，fatal之后的代码与watchdog均打印FAIL；缺失/重复/错误ID、额外error、返回继续、崩溃和超时都不能通过。官方库用 `$finish` 正常终止时仿真器可返回0，因此不将“任意非零退出”作为fatal支持证据。

### 行为变异验证与复现入口

`scripts/run_uvm_mutations.py` 要求当前完整有界 UVM 报告和所有 SV 变异目标通过，再复制独立 suite。v0.2 从七项 UVM 变异起步；v0.3 至 v0.7 加入仲裁/响应、配置、独占、停止和 RTL 复位变异。v0.8 加入约束随机化变异，v0.9 加入 UVM 随机事务交付变异。v0.10 验证载荷损坏、输出被阻塞时错移队首、同拍出入队计数错误、monitor 重发和漏发。

所有变异保持原Python参考不变。验收要求slang接受、编译成功、运行正常结束，且轨迹检查判fail，证据仍可复核；编译失败、工具崩溃、超时或缺工具均不计为“检测到行为错误”。源码与官方库不会被变异脚本修改。

```powershell
# 从源码目录运行；将已有的vlib/vlog/vsim加入PATH。
python -m fpga_agent sv-check --backend modelsim --output reports/uvm-profile.json --timeout 120
python scripts/run_uvm_mutations.py --baseline-report reports/uvm-profile.json --output reports/uvm-mutations.json
python scripts/run_sv_matrix.py --output reports/sv-uvm-matrix.json
```

### v0.2 状态与历史证据

报告schema3新增 `uvm_fixture_profile_status`：只有当前契约全部8个UVM case在**同一个后端**通过时才为pass；缺选、差距或失败均为not_complete。它描述有限夹具覆盖。`uvm_minimum_complete` 和 `full_sv_uvm_complete` 继续为false，不能把8个feature的小样本等同于最小UVM语义完备。版本/profile/后端/编译库和原始日志参与证据核验。

最终矩阵：根工作区 `validation/2026-11-17-uvm-profile-matrix.json` 及对应的两个后端报告；变异：`2026-11-17-uvm-mutations.json`；软件/交付指纹：`2026-11-17-uvm-summary.json`（均在validation）。首跑夹具错误和旧v0.1证据保留为历史；当前证据使用本轮新路径。

v0.2 第六轮尚无开源第二后端，随后接入的 Verilator 见 [运行边界与复现](verilator-runtime.md)。历史报告的套件和 checker 指纹对应当时版本。
