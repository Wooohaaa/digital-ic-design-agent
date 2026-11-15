# Tiny training-GPU compute core validation task — RSI roadmap stage 6

Build the forward-pass compute core of a **data-center training GPU**: a matrix
multiply engine (`C = ReLU(A * B)`) with on-chip SRAM for A, B and C, host
load/read ports, and a deterministic triple-nested loop over the N×N GEMM. The
backward pass and weight update live on the host (`fpga_agent/training.py`) —
this is the accelerator's forward compute.

The acceptance instance is `tiny_train_gpu #(N=4, DW=32)` in
`rtl/tiny_train_gpu.sv`. Keep the module name and ports unchanged. The host
loads matrices A and B, pulses `start`, the engine computes every C element in
`N^3` cycles, then `done` asserts.

The agent owns only `rtl/tiny_train_gpu.sv`. The host owns the fixed testbench
in `tb/tb_tiny_train_gpu.sv` and its SHA-256 pin in `project.json`; the
testbench holds the matrices and an independent 32-bit wraparound reference
model with ReLU.

## Scope boundary

This fixture exercises the stage-6 minimum: on-chip matrix SRAM, a GEMM
datapath, ReLU activation (with a `raw` bypass), deterministic iteration, and a
completion signal. It does **not** implement a warp scheduler, tensor cores,
mixed precision (FP8/BF16), sparse GEMM, high-bandwidth memory, or NVLink-class
interconnect. It is a single-tile integer GEMM, not a data-center training
accelerator, and the "Agent trains its own models" loop is demonstrated by the
host-side `fpga_agent/training.py`, not by silicon.
