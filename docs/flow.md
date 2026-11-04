# 设计流程与门禁

普通 RTL 模块采用下述自检流程；CPU 的可信验证 profile 见 [riscv-flow.md](riscv-flow.md)。CPU 模型只生成 RTL，参考模型/testbench/验收规则由验证侧固定。

```text
自然语言需求
     │
     ▼
需求/板卡资料 ──► LLM 生成 design.md、RTL、testbench
                               │
                               ▼
                    Icarus compile + 自检仿真
                         │ pass       │ fail
                         ▼            └─► 诊断回灌，最多 5 轮修复
                  Anlogic TD Tcl 工程
                         │
                   综合 / 布局布线
                         │
            reports + bitstream + 源码归档
```

## 阶段

1. **需求与板卡上下文**：工程包含需求 brief、器件型号、确认过的引脚和待核实字段。用户需求保存在 `spec/brief.md`。
2. **RTL 与验证**：模型要同时给出完整 RTL 与自检 testbench；agent 只写 `rtl/`、`tb/`、`spec/design.md`。验证目标从需求导出并记录到 `project.json`。
3. **仿真门禁**：普通模块由 Icarus 编译 `-g2012 -Wall` 并运行自检。CPU 使用独立指令级参考，逐条比较提交/存储/最终内存，并检查实际综合 SoC 的签名；单纯打印 PASS 不能代替 CPU 验证。
4. **综合和实现**：本地可信 Tcl 调用 TD `create_project/open_project/add_files/launch_runs/wait_run`。`--stage synth` 只综合；`--stage full` 运行 synthesis 与 place/route。
5. **交付**：完整交付要求当前输入、非负 setup/hold WNS、位流存在且 SHA-256 一致。RTL/约束/板级配置改变会让旧实现证据失效；testbench/参考模型改变要求重新仿真，但不要求对未改的硬件重新布局布线。仿真材料单独交付需 `--simulation-only`。
6. **实板验证**：用户通过 TD BitWriter 选择 SRAM 编程，按设计验收步骤检查外设；agent 当前不会控制下载器，也不写 Flash。

## 使用命令

```powershell
fpga-agent init <工程目录名>
fpga-agent design <工程目录> --brief-file requirements.md --repair-rounds 2
fpga-agent verify <工程目录>
fpga-agent td <工程目录> --stage synth
fpga-agent td <工程目录> --stage full
fpga-agent package <工程目录>
```

`design` 与 `verify` 都会落仿真报告。`td` 没有仿真通过的报告时会拒绝启动。模型生成的文本一律不作为 Tcl 或 shell 执行。

## 后续可扩展的验证强度

- 当前 RISC-V profile 已有 64 个固定随机种子、定向程序和独立参考模型；其它模块可复用该验证 profile 结构。
- Verilator lint 与仿真、formal equivalence/LEC、CDC 分析。
- TD post-route SDF 时序仿真和板上串口/逻辑分析仪自动验收。
- 对摄像头/DDR/HDMI 等板级 IP 项目读取并维护官方 TD 基线，限制 AI 只修改自有数据通路模块。

参考 RSI-Chip 的工程思想时，本项目保留 RTL + 自检仿真 + 明确 signoff gate 的路径；不将其 CPU/EDA 实现直接移植到 PH1P35。
