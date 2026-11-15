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
                         ▼            └─► 诊断回灌，冻结验证器，最多 5 轮 RTL 修复
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
fpga-agent design <工程目录> --brief-file requirements.md --repair-rounds 2 --provider api --model gpt-6-luna
fpga-agent verify <工程目录>
fpga-agent lec <工程目录>
fpga-agent formal <工程目录> --mode prove
fpga-agent synth <工程目录>
fpga-agent implement <工程目录> --target lattice-ice40-hx1k
fpga-agent targets
fpga-agent coverage <工程目录>
fpga-agent coverage <工程目录> --functional-coverage
fpga-agent cdc <工程目录>
fpga-agent timing <工程目录>
fpga-agent td <工程目录> --stage synth
fpga-agent td <工程目录> --stage full
fpga-agent release <工程目录> --version 0.7.0
fpga-agent package <工程目录>
```

`design` 与 `verify` 都会落仿真报告。`td` 没有仿真通过的报告时会拒绝启动。模型生成的文本一律不作为 Tcl 或 shell 执行。`lec`、`formal`、`synth` 由 Yosys 驱动，是可选的等价/属性/厂商中立综合证据门禁，`svsim` 与 `coverage` 由 Verilator `--binary` 驱动，分别是可选的 class 化（UVM 风格）验证环境门禁与可选 RTL 结构/功能覆盖门禁，细节见 [formal.md](formal.md)、[synth.md](synth.md)、[systemverilog.md](systemverilog.md)、[svsim.md](svsim.md) 与 [coverage.md](coverage.md)。`cdc` 对 RTL 做源码级时钟/复位域交叉**结构启发式**分析（不是 CDC 形式证明），见 [cdc.md](cdc.md)。`timing` 汇总布线后静态时序签核并尝试 post-route 仿真（当前环境无 `icesim`，如实记 `blocked`），见 [timing.md](timing.md)。`implement` 是厂商中立的多后端实现层，按 `project.json` 的 `target` 分派到 open iCE40、Anlogic TD 或通用综合后端，细节见 [backends.md](backends.md)；`targets` 列出内置目标 profile。`release` 聚合全部门禁并生成版本化交付 zip，见 [release.md](release.md)。`rtl/` 与 `tb/` 下的 `.v`、`.sv` 文件都会被收集、哈希并纳入证据；class 环境源由 manifest 的 `svsim` 块声明，实现目标由 manifest 的 `target` 块声明，覆盖计划由 manifest 的 `coverage` 块（顶层/源/cflags/functional_coverage）声明。默认 `coverage` 测的是 RTL **结构**覆盖，`--functional-coverage` 追加 `cover property` 功能点与并发断言检查；`DV-004` 仍保留 ASIC 物理签核为未关闭项。

最新的配置/依赖指纹、逐项需求检查、严格 lint、分职责工程审查和交付校验见 [engineering-workflow.md](engineering-workflow.md)。普通模块自动修复现在冻结已接受的 testbench/验收目标。现有工程仍可运行自检仿真，但缺少需求追踪或独立验证的部分会在审查报告中保留为阻塞项。

## 后续可扩展的验证强度

- 当前 RISC-V profile 已有 64 个固定随机种子、定向程序和独立参考模型；其它模块可复用该验证 profile 结构。
- 功能覆盖闭合（`icforge coverage --functional-coverage` 已提供 `cover property` 功能点与并发断言检查，见 [coverage.md](coverage.md)；断言**覆盖率**仍是未关闭项，`DV-004`）、更深的 CDC/RDC 形式证明（当前 `cdc` 是源码结构启发式，见 [cdc.md](cdc.md)）、与 TD post-route SDF 时序仿真（等价/属性检查见 [formal.md](formal.md)，厂商中立综合见 [synth.md](synth.md)，时序签核路径见 [timing.md](timing.md)）。
- TD post-route SDF 时序仿真和板上串口/逻辑分析仪自动验收。
- 对摄像头/DDR/HDMI 等板级 IP 项目读取并维护官方 TD 基线，限制 AI 只修改自有数据通路模块。

参考 RSI-Chip 的工程思想时，本项目保留 RTL + 自检仿真 + 明确 signoff gate 的路径；不将其 CPU/EDA 实现直接移植到 PH1P35。
