# TinyStories260K FPGA 工程试验

2026-11-04 实板通过：完整模型 CRC `53c34099`，1,536 个诊断 logits 最大绝对误差
`9.5367431640625e-06`，8 个生成 token 与独立参考全部一致。
开发板实际输出 `Once upon a time, there was a little girl`。
50MHz TD 结果：setup WNS 2.838ns，hold WNS 0.072ns，setup/hold TNS 均为零，STA coverage 99.99%。
hold 裕量较小；这些结果不替代 IO 预算、板卡版本及温压条件复核。
结构化结果见 [tinystories-results.json](tinystories-results.json)。

工作负载来自 [esp32-llm](https://github.com/DaveBben/esp32-llm)，使用
[tinyllamas 的 stories260K](https://huggingface.co/karpathy/tinyllamas/tree/main/stories260K)。
模型仓库声明 MIT；模型文件与 tokenizer 的 SHA-256 固定在工程清单中。
这里移植模型推理，采用 RV32I 软核执行 FP32 运算。

## 运行

```powershell
python -m pip install -e '.[tinyllm]'
icforge tinyllm benchmarks/tinystories260k --full
```

需要 RV32I/ILP32 GCC（支持 libgcc/libm）、本机 GCC、Icarus、Verilator、make 和安路 TD。
本机试验使用 PlatformIO 的 RISC-V GCC 与 MSYS2 UCRT 工具；不会把这些工具复制到仓库。
TUI 中可用 `/tinyllm tinystories260k --full`。

完整仿真通过后才启动 TD；TD 必须生成位流，setup/hold WNS 均非负且 TNS 为零，才能打包。
工程目录的 `deliverables` 保存交付 ZIP 和 SHA-256。

终端下载使用既有 AL-LINK-FT remote-bitbang 服务（端口 5555），需要 OpenOCD。
实板起初扫描到 Nuclei（IR5，ID `10300a6d`）和 PH1P35（IR8，ID `4808f0e7`）。
重配置期间 Nuclei TAP 消失，后续扫描只有 FPGA。下载器实时识别链结构，
用安路 BitWizard CLI 生成 SRAM SVF，由 OpenOCD 对 FPGA TAP 执行并严格检查 TDO。
若实测链结构从双 TAP 变为单 TAP，仅在重新扫描确认后重试一次。

```powershell
icforge tinyllm benchmarks/tinystories260k --program
icforge tinyllm benchmarks/tinystories260k --port COM5 --upload-model
# 无 COM 驱动时也可使用已安装的 FTDI D2XX B 通道：
icforge tinyllm benchmarks/tinystories260k --port ftdi://123456789B --upload-model
```

`--port` 复用已有有效的仿真、实现和下载证据，不重复运行仿真。
后续运行去掉 `--upload-model`，仍会读取整个模型并核验 CRC。
`--prompt 'Once upon a time' --new-tokens 8` 设置输入和生成长度。
下载成功与推理通过分别记录在 `reports/programming.json`、`reports/hardware.json`。

## 板卡连接与存储

以 HDL2604 文档及原理图为依据，完整来源摘要见 `board/hx1p35a.json`。
文档标题 A 与原理图 B 存在版本名称差异，应对照实物检查。

| 功能 | 引脚 |
|---|---|
| 系统 50MHz | C4 |
| UART RX / TX | J1 / H1 |
| 用户 N25Q128 CS / SCLK | D14 / N6 |
| 用户 N25Q128 MOSI / MISO | E13 / D16 |
| 用户 N25Q128 WP / HOLD | C16 / A13 |

**R15 是配置时钟 CCLK。R8、U12、V12、V11、U11 对应 GD25WQ128 配置 Flash。**
试验使用独立的 N25Q128，程序拒绝与预期 `20ba18` 不符的 JEDEC ID。
首次上传会擦写用户 Flash 起始约 1.01MiB；FPGA 配置只下载 SRAM。

## 验收与边界

1. 独立 NumPy 与原生 C 比较 64 个位置、32,768 个 logits。
2. 114 个 RV32I 程序，在指令/数据等待周期下比较提交、存储和最终内存。
3. 独立 SPI/UART 协议测试，包含字节序、握手与复位恢复。
4. 真实生产 ROM/固件在 Verilator 中运行完整 Transformer，经 SPI 引脚读模型、UART 引脚输出，比较 3 个位置、1,536 个 logits。
5. 实板需独立通过 JEDEC、完整 checkpoint CRC、1,536 个诊断 logits、生成 token 序列与 NumPy 参考比较。

部署使用 64-token 上下文（原始 checkpoint 支持 512），ROM 32KiB，RAM 128KiB。
全部推理由 FPGA 的 RV32I 软核完成；主机负责 tokenizer、显示与独立校验。
软件浮点和串行 Flash 带宽限制速度，适合作为功能验证基线。
时序报告、CDC/RDC、IO 预算和板卡版本仍需工程复核；自动门禁不代表 ASIC 流片签核。

## 本轮发现与修复

- SPI 接收计数器重复清零：独立测试首次读即超时，保留失败候选及 Luna 修复响应。
- Windows 工具链 DLL 冲突：隔离 GCC 与 Icarus 运行时顺序。
- ROM 文件路径：TD 快照提供来源相对路径别名并核验逐字节一致。
- 存储器综合：独立同步读寄存器，使 128KiB RAM 映射块 RAM。
- 配置与用户 Flash 混淆：最小工程定位 R15 约束触发 TD 崩溃；回查原理图修正连接。
- JTAG 链遗漏及变化：实际扫描识别两个 TAP，重配置后再次扫描确认单 FPGA TAP。
- Windows OpenOCD 大块发送失败：有序内存缓冲桥接慢速 USB 服务，保留逐项 TDO 校验。
- MISO 保持时间：保留违例报告，使用下降沿输入采样寄存器，在不放宽 IO 约束的条件下重新验证。

Luna 的实际候选与修复记录保留在 `generation`；exchange 导入日志明确标记为历史响应导入。
本机没有通过对外 API 冒充 Luna 调用，外部 API 的 502 与本轮工程试验分开处理。
