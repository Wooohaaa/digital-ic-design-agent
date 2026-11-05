# 多 Codex HDMI 旋转立方体工程试验

目标板为 HX1P35A / PH1P35MDG324，时钟50MHz，依据用户提供的 HDL2604 手册及原理图确定直连 HDMI 差分引脚。工程位于 `benchmarks/hdmi_cube/`。采用 640×480、25.2MHz 像素时钟、126MHz DDR 串行时钟，输出兼容 HDMI 显示器的 DVI/TMDS 视频；不包含音频或 HDCP。

## 实际分工

IC Forge 持久任务 `7269aae60e5a` 的主管和外部 Codex 执行者均显式指定 `gpt-6-luna`，并发上限6。六个独立进程分别负责时序、TMDS、三维几何、双缓冲绘制、安路 PHY 和补充 testbench；根据各任务的开始/完成时间戳计算，最高实际并发为6。随后执行顶层/约束集成及只读审查。原生会话、每次执行的日志、补丁和隔离工作树保留在 `.fpga-agent/tasks/7269aae60e5a/`（本地运行记录，不随仓库提交）。

立方体围绕 Y/X 两个轴进行固定点三维旋转，逐顶点投影，再用 Bresenham 绘制12条边。两个 640×480 一位片上帧缓冲交替清空/绘制，只在垂直消隐交换已完成缓冲。后台必须在420000个像素周期以内完成一帧，不依赖外部 DDR 或预存图像。

## 独立验收

宿主 `fpga_agent.video` 持有检查器，模型执行者不能编辑 `verification/` 或检查器源码。检查包括：

- 两个完整800×525光栅及消隐/同步边界。
- 全字节、连续差异和确定性随机 TMDS 序列，独立解码及实际传输位差异校验。
- 全部256个角度的8顶点，与独立整数规格及高精度旋转误差界限比较。
- 双缓冲启动、提前交换拒绝、绘制时限、越界读、复位恢复和三幅完整 RTL 帧；全部12条边与独立 Bresenham 参考比较。
- 三通道序列化的位序、字边界、差分极性和转发时钟。
- 从顶层差分端口恢复完整 TMDS/RGB/同步视频流，与独立模块捕获的帧逐像素对照，检查所有流水线对齐。
- 原生 TD 综合、布局布线、ERAM 映射、精确引脚电气标准及 setup/hold 时序门禁。

仿真报告与当前 HDL、规格、testbench、检查器哈希绑定。启动重验即使旧报告失效；重验失败留下失败报告。宿主验收调用 TD 时不改写输入 manifest 的状态字段，避免无意义的输入指纹变化触发整轮重跑。交付包同时收录并校验显式声明的规格和验证入口。

```powershell
python benchmarks/hdmi_cube/verification/acceptance.py
icforge verify benchmarks/hdmi_cube
icforge package benchmarks/hdmi_cube
```

## 本轮暴露的 agent 问题

1. 主管显式选模型时忽略原生 Codex 配置，导致认证通道与执行者不一致；已改为保留 provider 并显式传模型。
2. 批次集成树与名为 integration 的子任务目录冲突；已采用 workers/ 命名空间，并恢复本轮独立工作树，保留六个完成节点。
3. 分波等待会让就绪任务空等慢任务；已改为依赖就绪持续补位，并记录调度事件。
4. Windows 批处理补丁套壳可陷入 UTF-8/命令行长度/结束标记错误循环；已加入明确平台建议与有界自动恢复。
5. 暂停消耗了尝试次数；已分离真实失败、启动次数和工具恢复次数。
6. 长 JSON 事件可能被普通终端长行切片损坏；原生事件使用独立的有界预算。
7. 等待节点可能看不到批次期间新要求；现在派发前消费并持久保存补充要求。

实际运行结果、实现状态和硬件显示验收分别记录，不能把仿真帧当作实板照片，也不能把一次工程试验等同于对任意复杂任务或无限时运行的保证。当前显示器连接由用户稍后完成。

## 实际验收结果

独立入口 `benchmarks/hdmi_cube/verification/acceptance.py` 于 2026-11-05 全部通过，历时 103.39 秒：

- Icarus 仿真：光栅、TMDS、256 角度几何、三帧双缓冲、串行器和完整顶层 TMDS/RGB 恢复检查均通过。验收帧为角度 0、1、2；GitHub 预览另从最终 RTL 捕获角度 0、16、32，并明确标记为仿真结果。
- Anlogic TD 6.2.168.116 full：生成 `benchmarks/hdmi_cube/td/runs/run_20261105_141910_417955/hdmi_cube_full_Runs/phy_1/hdmi_cube_full.bit`。setup WNS +7.286 ns / TNS 0；hold WNS +0.073 ns / TNS 0；STA 覆盖率 99.88%。使用 832/21,216 LUT6、38/108 ERAM、4/40 DSP 和10个 PAD。
- 第一次 TD full 曾失败，原因是 `PH1P_PHY_PLL` 不支持 PH1P35MDG324。执行者核对本机安路原语模板、器件支持表及 PLL V2 模型后，改用 `PH1P_PHY_PLL_V2`，保留分频 5/63/25/5，并再次通过仿真和完整 TD 实现。最终像素时钟为25.2 MHz，串行 DDR 时钟为126 MHz。

本轮没有下载 bitstream，也没有拍摄或观察开发板 HDMI 输出；实际显示效果待用户稍后连接板卡和显示器后验证。独立报告位于工程的 `reports/simulation.json` 和 `reports/td.json`。交付归档由通过哈希门禁的 `icforge package benchmarks/hdmi_cube` 生成。
