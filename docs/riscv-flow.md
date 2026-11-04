# RISC-V CPU 设计与独立验证

本轮使用 **gpt-6-luna** 生成三份独立 CPU RTL：单周期、多周期、两级流水线。CPU 共享同一外部接口和执行环境，便于比较功能、周期数和 FPGA 资源。需求契约在 `fpga_agent/cpu_agent.py` 的 `CPU_CONTRACT`，设计说明在各工程 `spec/design.md`。

## 执行范围

RV32I 整数运算、移位、比较、分支、JAL/JALR、LUI/AUIPC、字节/半字/字读写、FENCE。执行环境采用小端存储、组合读存储器、时钟沿字节使能写；非对齐数据访问和跳转、ECALL、非法编码终止并置 trap。EBREAK 是测试结束标记，记录一次提交后停止。没有 M/C 扩展、CSR、中断或特权模式。

该执行环境适合验证整数 CPU 数据通路；结果不等同于完整 RISC-V 特权平台或官方架构兼容性认证。指令语义参考 [RISC-V RV32I 2.1 官方规范](https://docs.riscv.org/reference/isa/v20260120/unpriv/rv32.html)。

## 验证依据

`fpga_agent/riscv.py` 是独立的 Python 指令级参考模型和指令编码器，不使用候选 RTL 的译码/ALU 代码。`cpu_bench.py` 持有可信 testbench，不接受生成模型修改。

每份 CPU 运行 50 个定向程序和 64 个固定随机种子程序，逐项比对：

- 每条提交的 PC、机器码、写回寄存器和写回数据。
- 每次存储的地址、字节使能、有效数据，以及最终数据存储器。
- 终止/trap 状态和 watchdog。
- CPU 接入实际用于 TD 的片上 ROM/RAM 后，225 条提交产生的完整签名。

定向程序覆盖算术溢出、SRAI 符号扩展、SLTIU、x0、各字节通道、正负访存偏移、所有分支的取/不取、前后向跳转、JALR 清 bit0、rd=rs1、错误路径存储抑制和非对齐/非法指令。

这是可重复的回归集合，尚未接入 Spike、官方 riscv-arch-test 或形式证明；这些适合作为下一层独立验证。

## 模型后端

本次实际生成和修复来自当前会话的 `gpt-6-luna` 子代理，随后通过 `exchange` provider 导入 agent。`generation/luna-response.json` 保存完整响应，`generation/design_round_00/` 保存请求、响应和 provenance。该路径暂时代替付费 API 接入。

本机独立 Codex CLI 使用 ChatGPT 账户调用 `gpt-6-luna` 时返回 `model_not_found`，因此本轮没有把 CLI 或其它模型的结果冒充 Luna。`api` provider 已实现 OpenAI Responses API，可在配置 API Key 后改用；`chat` 是显式兼容接口。

```powershell
# 用本轮保存的真实 Luna 响应重现完整流程
python -m fpga_agent cpu-design benchmarks/rv32_single --architecture single --provider exchange --response-file benchmarks/rv32_single/generation/luna-response.json --full
python -m fpga_agent cpu-design benchmarks/rv32_multi --architecture multi --provider exchange --response-file benchmarks/rv32_multi/generation/luna-response.json --full
python -m fpga_agent cpu-design benchmarks/rv32_pipeline --architecture pipeline --provider exchange --response-file benchmarks/rv32_pipeline/generation/luna-response.json --full

# 重新验证已有三份工程（--full 会再次执行 TD）
python -m fpga_agent cpu-suite
python -m fpga_agent cpu-suite --full

# 将来切换为独立 API 请求，模型保持 gpt-6-luna
$env:OPENAI_API_KEY = '你的 Key'
python -m fpga_agent cpu-design my-cpu --architecture multi --provider api --model gpt-6-luna --full
```

## 本轮修复

多周期核第一次运行在 `SRAI x25,x1,31` 失败：x1=0x80000000，结果为 1，应为 0xffffffff。Luna 把有符号移位从无符号三目表达式中分离，随后又修复 LW 写 x0 时提交数据没有清零的问题。原始 RTL、首个失败程序和实际输出保存在 `benchmarks/failures/rv32_multi_initial/`；修复记录在 `benchmarks/rv32_multi/generation/repair_001.json`。

规范复核又补充了 FENCE 前向兼容测试：基础 RV32I 必须忽略 FENCE 的 rd/rs1 以及保留模式字段，将其作为普通 FENCE。单周期和多周期核原先会误 trap，Luna 修复后，两份核都重新通过 114 程序及 TD 实现。反例和修复前 RTL 保存在各工程 `generation/fence_failure/`，修复记录分别为单周期 `repair_001.json` 和多周期 `repair_002.json`。

agent 同时修复了候选文件先写后验证、ANSI 向量端口继承、重复引脚绑定、timeout 字节输出处理、缺失 bitstream、非有限/负 WNS 以及旧报告复用问题。可信 SoC/testbench 由代码重新生成比对，不由模型或可改的 manifest 指纹单独背书。编译/实现期间输入被修改也会使证据失效。只有当前源码和可信参考模型验证通过，且 TD 时序收敛、bitstream 指纹一致，才生成默认交付包。

## HX1P35A 集成

顶层为 `rv32_soc`，内部 ROM、RAM 和提交签名把 CPU 的宽总线保留在芯片内部，只向板上引出系统时钟 C4、复位 R10、screen PWM R11。运行结束后输出序列化签名，方便后续用逻辑分析仪读取。时钟约束为 50 MHz。

本轮只验证本地仿真、TD 综合/布局布线、时序和 bitstream。没有下载到开发板；上述引脚仍需按 HX1P35A 的实际 revision 与官方原理图确认。
