# TinyStories260K：HX1P35A 多轮实板生成

2026-11-04 在 HX1P35A 上重新下载 TinyStories SRAM 位流，并使用开发板 UART 连续运行多轮生成。模型保存在独立 N25Q128；本轮只下载 FPGA SRAM，没有重写模型 Flash。

三轮生成全部通过：每轮模型 Flash CRC 都是 `53c34099`，1,536 个诊断 logits 与独立 NumPy 参考对比通过，最大绝对误差 `9.5367431640625e-06`，生成 token 序列逐 token 匹配。

## 板上生成文本

以下内容来自实板 UART 返回并经参考模型校验。每次请求上限为 16 个新 token；模型尚未输出 EOS 的文本会在上限处截断，所以这里保留的是原始生成片段。

### 1. 小狐狸

提示词：`Once upon a time, a little fox`

> Once upon a time, a little fox named Benny. Benny loved to play with his

### 2. 小女孩

提示词：`A little girl`

> A little girl named Lily went to the park with her mom. She saw a

### 3. 小狗

提示词：`A small dog`

> A small dog named Spot was a boy who loved to play

逐轮时间、token ID、CRC 和 logits 误差见 [结构化测试记录](tinystories-rounds.json)。

## 未通过的一轮

提示词 `In a small village, there lived a boy` 被编码成 22 个 token。模型 CRC 和 logits 检查通过，但板子在首个生成 token 返回前超过 UART 的 240 秒空闲等待时间，故该轮标记为超时，没有把它当成生成结果。后续三个短提示词轮次均通过。这暴露了长提示词预填充时间与固定串口等待上限不匹配。

## 复现

```powershell
icforge tinyllm benchmarks/tinystories260k --port ftdi://123456789B --prompt 'A little girl' --new-tokens 16
icforge tinyllm benchmarks/tinystories260k --port ftdi://123456789B --prompt 'A small dog' --new-tokens 16
```

每次运行都重新核验 Flash CRC 和 1,536 个诊断 logits，再将板上生成 token 与独立 NumPy 参考逐项比较。这里只展示文本与机器可读记录，不嵌入图片。
