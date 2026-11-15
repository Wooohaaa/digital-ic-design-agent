# Tiny GPU (RISC-style SIMT inference core) validation task — RSI roadmap stage 4

Build a **minimum inference-only GPU**: a lockstep SIMT core with a small
**PTX-like instruction subset** (the CUDA-compatible path of roadmap stage 4).
It is a fixed-function MAC engine for inference kernels (e.g. dot products),
not a general-purpose/schedulable GPU.

The acceptance instance is `tiny_gpu #(LANES=4, NREG=8, IMEM_DEPTH=64,
DMEM_BANK_DEPTH=16, DW=32)` in `rtl/tiny_gpu.sv`. Keep the module name and
ports unchanged. The host loads the program and data, pulses `start`, and the
core runs every lane in lockstep until `EXIT`, then raises `done`.

The agent owns only `rtl/tiny_gpu.sv`. The host owns the fixed testbench in
`tb/tb_tiny_gpu.sv` and its SHA-256 pin in `project.json`; the testbench holds
the program, the per-lane input vectors and an independent reference model.

## Scope boundary

This fixture exercises the stage-4 minimum: a tiny PTX-like ISA (mov/mov/add/
sub/mul/mad/ld/st/setp.lt/bra/exit), a per-lane register file, a per-lane data
bank, and lockstep execution. It does **not** implement a warp scheduler,
divergent control flow, caches, a memory hierarchy, or a real CUDA runtime.
