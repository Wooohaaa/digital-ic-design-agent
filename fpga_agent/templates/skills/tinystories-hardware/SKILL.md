---
name: tinystories-hardware
description: TinyStories260K 在 HX1P35A 的 FP32 推理证据、Flash CRC、UART 结果和长提示词预填充问题。
---
# TinyStories engineering evidence

1. Inspect docs/tinystories-flow.md and the current project's manifest, firmware contract and reports. The deployment profile uses a 64-token context and an RV32I softcore executing FP32 inference.
2. tinyllm verification/full covers independent reference, production RTL inference and optional TD/package. Physical programming and UART acceptance require their own reports.
3. Model/tokenizer hashes, JEDEC ID and full Flash CRC must match the declared assets before inference is accepted. Never confuse user model Flash with FPGA configuration Flash.
4. Physical acceptance compares diagnostic logits and the full generated token sequence with an independent reference; decoded text alone cannot establish numerical correctness.
5. Retrieve prior TinyStories failures with recall. A long prompt can spend several minutes in prefill before its first generated token, exceeding a fixed UART idle timeout. Separate timeout diagnosis from a logits/token mismatch.
6. Preserve failed attempts and the actual generated text. State generation limits and whether EOS was reached, rather than presenting a token-limited fragment as a finished story.
