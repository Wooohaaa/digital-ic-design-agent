# 综合与 LEC：当前范围

日期：2026-11-17。实现：`fpga_agent/lec.py`。CLI、终端 `/lec`、Agent typed action 和长期任务共享同一入口。

同日第二轮新增显式 `state-bijection` 时序证明，默认 `direct` 组合路径保留。证明契约、初态/异步复位和适用边界见 [时序 LEC](lec-state.md)。
同日第三轮新增 [显式复位后状态关系](lec-reset.md)，支持合并状态；schema 升为 3。三种 CPU 工程均启用必选的 core LEC，报告区分证明目标和 SoC 顶层。

```powershell
python -m pip install yowasp-yosys==0.71.0.0.post1285
python -m fpga_agent lec examples/lec/adder
# 使用项目内已有 Verilog 网表进行反例/外部网表检查
python -m fpga_agent lec examples/lec/adder --netlist candidate.v --timeout 120
```

优先发现 PATH 中的 `yosys`、`yowasp-yosys`，也识别当前 Python 虚拟环境内的 YoWASP；可用 `--yosys` 指定可执行文件。工具版本保存于每份报告，不自动安装工具。

## 流程与门禁

1. 读取 `project.json` 中 `top_module`、`rtl_sources`，检查路径，追踪 literal includes 和内存初始化文件；把输入与配置 SHA-256 绑定到本次运行。
2. 将输入复制到独立 `build/lec/<run-id>/inputs/`；Yosys SV 子集前端展开并产生 RTLIL golden。
3. `synth -flatten -noabc` 生成 generic-cell Verilog 网表；使用 `--netlist` 时跳过这步并绑定所给项目内网表。
4. 默认 direct：`equiv_make` 后执行 `equiv_simple -undef` 和 `equiv_status -assert`。所有退出状态成功且 JSON 中每个 `$equiv` 的 A/B 已合并、存在可观察输出及非空证明点，才接受。state-bijection：检查完整状态双射和初态集合、异步复位事件，再用组合 SAT 证明全部下一状态及输出义务。
5. `reports/lec.json` 记录状态、命令、工具版本、输入、证明点和产物指纹。`lec_valid` 重验输入、checker、所有必要产物及证明清单；改源码、include、配置、脚本或产物都会使旧证据失效。

配置 `"formal_equivalence": {"required": true}` 后，LEC 会影响工程审查的 artifact readiness、长期任务完成判断与交付门禁；TD 公共入口在仿真门禁后复用有效证明或自动重跑，覆盖 flow/CPU full/直接 td。交付携带原始 LEC 报告及其网表、脚本、快照和日志，打包前后复核字节指纹。`engineering_signoff` 仍不授予。

返回码：0=pass，2=工具缺失 blocked，1=失败/超时/未证明/输入错误。`fail` 表示验收未通过；未证明本身不能推出 RTL 存在功能错误。

## 已知边界

两槽 ready/valid FIFO 已按完整状态双射验证；对应的 [UVM 仿真和网表重放](fifo-flow.md)绑定同一源文件及同一生成网表。

- 首轮验收确认了组合电路：加法器及测试中的 XOR；错误加减网表被拒绝。加法器另外穷举全部 131,072 种输入验证规格。
- 首轮“计数器/FIFO 未证明”已被同日新增 state-bijection 正例取代：显式选择该方法可证明计数器、FIFO、带初始化的 RAM 和两个现有 RV32I 核（单周期/两级流水）；默认 direct 仍不接受未证明的时序点。双射不覆盖状态合并；第三轮显式 reset-state-relation 已使多周期核在复位后契约下通过。重定时、重编码、多时钟和完整 SoC 证明仍未覆盖。
- 不使用 `equiv_induct` 弱时序归纳，不开启忽略未知单元或环境假设。Yosys 对其弱等价定义有[明确说明](https://yosyshq.readthedocs.io/projects/yosys/en/v0.54/cmd/equiv_induct.html)；本机 0.71 的命令帮助也已核对。
- golden 和 synthesis 共享 Yosys 前端，因此 LEC 不证明前端 SV 语义正确；还需要独立语义差分。`-undef` 按 Yosys 模型处理未定义值，不能解释成完整四态 SV 仿真一致性。
- 当前没有 ASIC 标准单元库/Liberty mapping、工艺延迟、STA、物理设计或 UVM 实现。外部网表仅支持 Yosys 可理解的 Verilog 单元，未知技术单元不会通过证明。
- 暂只支持默认参数和 portable literal 路径；无 include-dir/宏定义/参数覆盖 CLI。报告指纹用于追踪本地可信工具链的变化，不是第三方签名的证明证书。

测试覆盖工具缺失、超时、组合正反例、语法错误、空输出、时序未证明拒绝、include 失效、产物篡改、Agent 完成门禁和交付还原。
新增状态测试覆盖初态、复位事件、全部下一状态/输出、反例文件、映射删减和时序交付还原。详细产物使用 schema 2，旧报告须重跑。
