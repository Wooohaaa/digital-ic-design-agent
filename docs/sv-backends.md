# SV/UVM 第二运行后端与差异证据

2026-11-17，第五轮。接续 [首版能力契约](sv-conformance.md)，实现 `sv-check --backend icarus|modelsim`。默认仍为 Icarus；选择显式、失败不切换后端，报告也不合并不同后端的能力。
第六轮接续：[官方UVM有界运行契约v0.2](uvm-profile.md) 已补齐这里尚planned的6类夹具，扩展为18个case；本页保留第五轮观测与schema2历史，当前报告使用schema3。

本机 ModelSim SE-64 10.6e 的 11 个现有用例全部通过：13 个限定 feature 为 fixture_verified，另外 6 个 UVM 目标仍 planned。**全 SystemVerilog、UVM 最小子集、自研 EDA 完备均未实现**。这里的运行能力来自外部工具，ModelSim 需要用户已有安装与可用许可；项目不包含或安装该工具。

| 检查 | Icarus 13.0 | ModelSim 10.6e | 独立参考 |
| --- | --- | --- | --- |
| 8 个基础用例（含四态和语法错误拒绝） | pass | pass | 各自逐行比对 Python oracle，slang 独立静态检查 |
| SV-CLASS：base、derived、base handle 虚调用 | fail：12、18、9 | pass：12、18、18 | 12、18、18；未改动期望或夹具 |
| SV-INTERFACE：interface/modport 端口 | gap：编译拒绝 | pass：64 行匹配 | 8 位加法参考 |
| UVM-PHASE：build/run/report 与 objection | gap：编译拒绝 | pass：3 行匹配 | 固定顺序；官方 2020.3.1 完整库，UVM_NO_DPI |

双后端总结果保持 **fail**，表示其中一个后端仍存在语义失败；单独 ModelSim 报告为 pass。8 项基础用例均被两个仿真器实际运行/拒绝并各自对照参考；另 3 项能力状态有差异。slang 始终只做解析、类型检查和 elaboration。

## 运行与证据

```powershell
# 将已有 vlib/vlog/vsim 以及 iverilog/vvp 所在目录加入 PATH。
python -m fpga_agent sv-check --backend modelsim --output reports/modelsim.json
python scripts/run_sv_matrix.py --output reports/sv-dual.json
# Agent typed arguments: action=sv-check, sv_backend=modelsim
# TUI: /sv-check --backend modelsim
```

ModelSim 使用独立工作库，`vlog -sv` 编译，`vsim -c -novopt` 运行；本机默认优化加载失败，因此明确选择禁用优化并保留 warning。运行脚本在错误时以非零退出，超时和崩溃不算成功。程序不会采用仿真器自带的另一个 UVM 版本：锁定的官方库与 fixture 源码被明确传给 vlog。

报告 schema 2 绑定选定 backend、版本、命令和运行观察；记录原始 transcript、工作库文件、输入/检查器/产物哈希。解析时识别 ModelSim 的 `# ` 前缀、`** Error/Fatal`，不改写原始日志。验证器拒绝后端冒用、工作库证据缺失、跨 case 观察替换和其他证据变化。

`run_sv_matrix.py` 为每个后端生成独立报告，调用 `conformance_valid` 重验，再比较相同输入下的状态；总体失败不会吞掉某个后端的 pass，也不会掩盖另一个后端的 fail。返回码 0=全部选定后端通过，1=语义失败/证据失效/输入不一致，2=差距或缺工具。缺许可、缺工具和前端拒绝均不能升级为支持。

工作区最终证据：`validation/2026-11-17-sv-dual-final.json` 及同名前缀的 `-icarus.json`、`-modelsim.json`；运行/构建与代码指纹汇总见 `validation/2026-11-17-dual-final-summary.json`。第五轮初次双后端结果另保存在 `2026-11-17-sv-dual.json`，随后完整编译库库存校验加固，由 final 报告接续。第四轮 schema 1 报告也作为历史快照保留；检查器已升级，不能把旧报告当作当前检查器的有效证据。

## 下一步边界

第二后端确认了原始反例并提供可选执行路径，没有修复 Icarus。下一步补齐官方 UVM component/factory/config/analysis/sequence/reporting 的独立运行夹具与负例；随后加入可在开源 CI 执行这些探针的运行后端。远端 CI 本轮未执行，当前 CI 仍只要求 Icarus 基础子集；商业后端不可视为公共 CI 的默认依赖。
