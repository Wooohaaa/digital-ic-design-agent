# TinyStories260K on HX1P35A

FP32 checkpoint: dim=64, hidden=172, 5 layers, 8 query heads, 4 KV heads, vocabulary=512.
All embedding, RMSNorm, GQA causal attention, RoPE, SwiGLU and classifier computation executes
in a Luna RV32I softcore with libgcc software float. Model weights are read-only mapped from
the independent 16MiB N25Q128 user QSPI NOR at 0x10000000. The FPGA configuration Flash is not addressed.
ROM=32KiB at 0; RAM=128KiB at 0x20000000. KV uses 80KiB for 64 tokens, not the checkpoint's
512-token context. No quantization, retraining, Xtensa ISA emulation, or ASIC fabrication.
50MHz single clock; SPI mode0 12.5MHz; UART115200 8N1 with two-flop RX synchronizer.
Power-on reset uses an initialized 8-bit counter and releases synchronously. UART is asynchronous;
SPI MISO follows the generated SPI timing. The SDC IO budgets require hardware review.
UART monitor G takes prompt token IDs and total forward steps. T also emits all 512 logits.
Tokenization and text decoding are host I/O; inference runs on FPGA. W stop-and-wait programs
only the independent user Flash, checks JEDEC/capacity, bounds, busy watchdog and CRC readback.
Actual hardware success requires a current bitstream, full image CRC match and board UART
logits/token evidence; offline simulation does not grant hardware deployment status.
Reference: karpathy/llama2.c and karpathy/tinyllamas (MIT); DaveBben/esp32-llm for workload.
The manual/pin table naming A/B differs: inspect physical revision before programming.
