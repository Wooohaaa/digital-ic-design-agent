# Tiny LPU (tensor streaming inference processor) validation task — RSI roadmap stage 5

Build a **minimum inference-only LPU** (tensor streaming processor) for the
RSI roadmap stage 5: commercial-grade performance with a new architecture for
extreme inference optimization, running open models. This is the first piece of
the RSI loop: an on-chip weight SRAM, streaming activations through parallel MAC
units, deterministic execution. It is a fixed-function GEMV engine, not a
general-purpose GPU or a datacenter training GPU.

The acceptance instance is `tiny_lpu #(NMAC=4, NW=16, DW=32)` in
`rtl/tiny_lpu.sv`. Keep the module name and ports unchanged. The host loads
weight banks and streams activations; the engine accumulates dot products in
lockstep and asserts `done` after `NW` activations.

The agent owns only `rtl/tiny_lpu.sv`. The host owns the fixed testbench in
`tb/tb_tiny_lpu.sv` and its SHA-256 pin in `project.json`; the testbench holds
the weight vectors, activation streams and an independent reference model.

## Scope boundary

This fixture exercises the stage-5 minimum: on-chip weight SRAM, streaming
activation input, parallel MAC array, deterministic lockstep execution, a shared
index counter, and a streaming-complete `done` signal. It does **not** implement
a warp scheduler, divergent control flow, caches, a memory hierarchy, sparse
tensors, mixed precision, or a real LPU runtime. It is a single-layer GEMV
engine, not a multi-layer transformer inference system.
