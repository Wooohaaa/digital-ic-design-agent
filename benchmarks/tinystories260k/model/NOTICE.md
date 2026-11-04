# Model provenance

Checkpoint/tokenizer: https://huggingface.co/karpathy/tinyllamas/tree/main/stories260K
Model card: https://huggingface.co/karpathy/tinyllamas/blob/main/README.md (license: mit).
Hashes and architecture: manifest.json.
Inference architecture reference: https://github.com/karpathy/llama2.c (MIT; license included).
ESP32 workload reference: https://github.com/DaveBben/esp32-llm.
The checkpoint is unchanged FP32. This deployment limits the runtime context to 64 tokens.
