# 同一份 FIFO RTL：从 UVM 仿真到网表

`UVM-RTL-FIFO` 编译 `fpga_agent/templates/conformance/rtl_fifo/fifo_two.sv`。合成使用同一文件的逐字节快照，LEC 工程的 `project.json` 与该文件同目录。测试平台只定义激励和监视，工程没有第二份手写 FIFO。

在源码仓库目录运行，并让本地 ModelSim 和 Yosys 或 YoWASP 可用。官方 UVM 仍由已固定源文件清单验签。

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m fpga_agent sv-check --backend modelsim --case UVM-RTL-FIFO --output reports/fifo-rtl.json
python scripts/run_fifo_flow.py --rtl-report reports/fifo-rtl.json --output reports/fifo-flow.json
```

流程先重验传入报告及编译源清单，再合成通用 Verilog 网表，执行 [state-bijection LEC](lec-state.md)，并把生成的网表替换进一份独立套件，使用同一 UVM 夹具和 Python 检查器复跑 16 拍。运行后记录 RTL、证明、网表及重放报告的指纹。额外反转一份网表的 `in_ready` 输出，要求前置阶段成功且 SAT 返回实际反例（`proof_ok=0`）。证据记录本地编译路径；换目录或机器后应重新运行。

2026-11-20 本机验证使用 Yosys 0.71 和 ModelSim。LEC 覆盖全部 36 个对应状态位和 18 个输出位，比较相同的允许初态集合、任意对应 Boolean 状态下的下一状态（含同步复位）及输出。网表重放在相同反压计划中交付五笔事务；随机载荷按各次运行的合法解检查。这是单时钟通用网表的等价和零延迟仿真证据；没有工艺库映射、STA 或物理签核。两侧形式模型共用 Yosys 前端，独立行为检查的范围由固定夹具决定。

满队列同步复位使用同一 DUT，无需更改综合工程：

```powershell
python -m fpga_agent sv-check --backend modelsim --case UVM-FIFO-SYNC-RESET --output reports/fifo-reset-rtl.json
python scripts/run_fifo_flow.py --case UVM-FIFO-SYNC-RESET --rtl-report reports/fifo-reset-rtl.json --output reports/fifo-reset-flow.json
```

用例覆盖一次低有效同步复位：队列已满，第三个写请求受阻且接收端 ready。复位优先于传输，队列在上升沿清空，scoreboard 不接收旧队首；UVM 停止旧 sequence，复位后复用 ID 70、72，接收载荷 100、101。此场景只验证单时钟、一次复位和一个受阻的 UVM 请求。

两轮竞争复位用例 `UVM-FIFO-REPEAT-RESET` 可替换上述两条命令中的 case ID。每轮有一条持锁 sequence、一条等待授权的竞争者；第二轮复位后两条普通 sequence 完成。两项复位用例共享同一个 DUT 和综合契约。

使用 Icarus 复现 24 个端口边界场景和网表重放，无需 UVM 源文件：

```powershell
python -m fpga_agent sv-check --backend icarus --case SV-FIFO-RESET-PRIORITY --output reports/fifo-priority-rtl.json
python scripts/run_fifo_flow.py --case SV-FIFO-RESET-PRIORITY --rtl-report reports/fifo-priority-rtl.json --output reports/fifo-priority-flow.json
```

边沿前可同时看到两端握手条件为真。同步复位有效时，FIFO 在边沿清空，边沿前的 valid/ready 不视为实际传输；接收端也必须遵守复位规则。测试比较有无复位的对照，并用新数据排查复位后读写指针重新对齐是否正确。

[独立队列规格证明](fifo-spec-proof.md)在同一 RTL 上覆盖首次同步复位后的任意输入序列，和本网表等价流程分别出具报告。
