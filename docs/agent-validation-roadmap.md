# IC Forge agent validation projects

These fixtures exercise six different design styles while pinning the host-authored acceptance harness. The coding agent may edit only `rtl/*.v` or `rtl/*.sv`; it must not change the specification, testbench, manifest, or acceptance checks.

| Project | Engineering focus | Required verification |
|---|---|---|
| `sync_fifo` | State, pointer wrap, simultaneous operations, full/empty policy | Icarus self-checking scoreboard, deterministic directed and pseudo-random traffic, strict Verilator lint, CDC analysis |
| `spi_master` | Parameterized serial timing and edge semantics | All four CPOL/CPHA modes, MSB-first TX/RX loopback model, multi-transfer checks, strict lint |
| `apb_pwm_timer` | APB setup/access timing, register side effects, timer/PWM/IRQ | Register read/write, invalid-address response, PWM boundary duty cases, interrupt set/clear checks, strict lint |
| `sv_datapath` | SystemVerilog subset: `always_ff`/`always_comb`, `typedef enum`/`struct packed`, `$clog2`, `generate` | Icarus self-checking scoreboard, strict lint, Yosys synthesis and RTL-vs-netlist LEC (see [SystemVerilog support](systemverilog.md)) |
| `sv_class_env` | Minimal class-based (UVM-style) environment: transaction, `virtual interface` driver, scoreboard | Icarus self-checking scoreboard plus a Verilator `--binary` class environment (`icforge svsim`, see [svsim](svsim.md)) |
| `ice40_pwm` | Vendor-neutral implementation on an open device: counter-driven PWM duty + level nibble | Icarus self-checking scoreboard, strict lint, Yosys synthesis, LEC, the open iCE40 place-and-route + bitstream flow (`icforge implement`, see [backends](backends.md)), Verilator structural coverage (`icforge coverage`, see [coverage](coverage.md)), CDC analysis (`icforge cdc`), and post-route timing signoff (`icforge timing`, see [timing](timing.md)) |
| `cdc_dual_clk` | Cross-domain signal transfer across two clocks | Icarus self-checking scoreboard, strict lint, and clock/reset-domain crossing analysis (`icforge cdc`, see [cdc](cdc.md)) — expected `review_required` |
| `functional_cov` | Functional (`cover property`) coverage and concurrent-assertion checking on a 4-bit counter | Verilator `--binary --coverage` with `--functional-coverage` (`icforge coverage --functional-coverage`, see [coverage](coverage.md)) — structural 20/20, functional 4/4, no assertion failure |
| `tiny_gpu` | RSI stage 4: lockstep SIMT tiny GPU with a PTX-like instruction subset (movi/mov/add/sub/mul/mad/ld/st/setp.lt/bra/exit), per-lane register file and data bank, 4-element dot-product MAC kernel | Icarus self-checking scoreboard with independent 32-bit wraparound reference model, strict Verilator lint, Yosys synthesis, Verilator structural coverage (100% RTL points), CDC analysis (see [ROADMAP](../ROADMAP.md)) |
| `tiny_lpu` | RSI stage 5: tensor streaming LPU with on-chip weight SRAM, parallel MAC array, streaming activation input, deterministic lockstep execution | Icarus self-checking scoreboard with independent 32-bit wraparound reference model, strict Verilator lint, Yosys synthesis, Verilator structural coverage (100% RTL points), CDC analysis (see [ROADMAP](../ROADMAP.md)) |
| `tiny_train_gpu` | RSI stage 6: training-GPU forward compute core — on-chip A/B/C SRAM, host load/read ports, triple-nested integer GEMM with ReLU activation (and a `raw` no-ReLU bypass), deterministic iteration | Icarus self-checking scoreboard with independent 32-bit wraparound reference model, strict Verilator lint, Yosys synthesis, Verilator structural coverage (100% RTL points), CDC analysis (see [ROADMAP](../ROADMAP.md)) |
| `tiny_soc` | RSI stage 7: minimal SoC-level integration with an RV32I CPU and a DMA engine (2D scatter/gather with row strides + descriptor chains + interrupt) as two bus masters — AXI-like handshaked memory-mapped host bus with address decoder (`bus_wready` declares the operand-SRAM write port available, `bus_rvalid` reflects combinational reads), a direct-mapped blocking instruction cache in the CPU fetch path (a miss asserts `imem_stall` and the wrapper gates the CPU clock while a NOP is served during refill) plus a direct-mapped non-blocking data cache in the CPU data path (a miss bypasses to SRAM in the same cycle while the line refills; coherence by invalidation on host/DMA writes and on GEMM start), on-chip GEMM accelerator (`C = ReLU(A*B)`) with A/B/C SRAM, an 8N1 UART transmitter for serial I/O, and a DMA engine that copies row-by-row with configurable source/destination row strides, walks host-programmed descriptor chains, and raises an interrupt on completion | Icarus self-checking scoreboard with independent 32-bit wraparound reference model and per-cycle UART capture/decode, an RV32I program that drives the bus itself (REQ-008), DMA linear copies with word-by-word verification against diverse 32-bit source data (REQ-010/011), a 2D scatter/gather copy verified word-by-word against the gathered source (REQ-012), DMA interrupt assert/clear/reassert behavior (REQ-013), a descriptor-chain copy verified word-by-word with a single end-of-chain interrupt (REQ-014), an AXI-like handshaking test verifying write-ready drops during a GEMM run and a raw operand-SRAM write is dropped (REQ-015), an instruction-cache test verifying a cold miss refills a line (the CPU is stalled and a NOP served mid-refill, and the bypassed IMEM word is read back), a hot probe is a clean hit with no refill, and tags distinguish blocks mapping to the same line (REQ-016), a data-cache test verifying a cold probe misses and bypasses to SRAM, an explicit fill makes every offset of the line a hit, a host write invalidates the line and the next probe misses to the new value, B/C tags distinguish windows mapping to the same line, and a GEMM run flushes the cache (REQ-017), strict Verilator lint, Yosys synthesis, Verilator structural coverage (100% RTL points, 19 documented unreachable waivers), CDC analysis (see [ROADMAP](../ROADMAP.md)) |

Each fixture has a trusted testbench SHA-256 pin. Simulation evidence is invalidated if an implementation agent or later edit changes the testbench. The local flow is Icarus simulation followed by Verilator `-Wall`; the SystemVerilog fixture additionally pins the vendor-neutral Yosys synthesis and LEC stages, the class-environment fixture pins a Verilator `--binary` run, the `ice40_pwm` fixture pins the open iCE40 synthesis -> place-and-route -> bitstream backend plus structural coverage, CDC and timing steps, the `cdc_dual_clk` fixture pins a CDC `review_required` result, and the `functional_cov` fixture pins structural plus functional coverage with a clean assertion result. Anlogic TD synthesis is a separate synthesis-only gate because these blocks have no approved HX1P35A pin map. Full place-and-route, post-route simulation, timing signoff, bitstream programming, and board behavior are not claimed by this suite (the `ice40_pwm` bitstream is real but not programmed or board-verified; `icesim` is unavailable so `icforge timing` reports post-route `blocked`).

## Host acceptance sequence

For each project, run:

```powershell
python -m fpga_agent verify examples/agent_validation/<project>
python -m fpga_agent lint examples/agent_validation/<project>
python -m fpga_agent review --scope simulation examples/agent_validation/<project>
python -m fpga_agent td --stage synth examples/agent_validation/<project>
```

For `sv_datapath`, additionally run the vendor-neutral Yosys stages:

```powershell
python -m fpga_agent synth examples/agent_validation/sv_datapath
python -m fpga_agent lec examples/agent_validation/sv_datapath
```

For `sv_class_env`, additionally run the class-based environment:

```powershell
python -m fpga_agent svsim examples/agent_validation/sv_class_env
```

For `ice40_pwm`, additionally run the vendor-neutral open implementation backend:

```powershell
python -m fpga_agent synth examples/agent_validation/ice40_pwm
python -m fpga_agent lec examples/agent_validation/ice40_pwm
python -m fpga_agent implement examples/agent_validation/ice40_pwm
python -m fpga_agent coverage examples/agent_validation/ice40_pwm
python -m fpga_agent cdc examples/agent_validation/ice40_pwm
python -m fpga_agent timing examples/agent_validation/ice40_pwm
python -m fpga_agent release examples/agent_validation/ice40_pwm --version 0.7.0
```

For `functional_cov`, additionally run the functional coverage stage:

```powershell
python -m fpga_agent coverage examples/agent_validation/functional_cov --functional-coverage
```

For `tiny_gpu` (RSI roadmap stage 4), run the full vendor-neutral EDA stack. `icforge lec` is not applied here: the multiplier-dense netlist exceeds the WASM Yosys SAT budget (300s timeout), a documented scaling limit.

```powershell
python -m fpga_agent verify examples/agent_validation/tiny_gpu
python -m fpga_agent lint examples/agent_validation/tiny_gpu
python -m fpga_agent synth examples/agent_validation/tiny_gpu
python -m fpga_agent coverage examples/agent_validation/tiny_gpu
python -m fpga_agent cdc examples/agent_validation/tiny_gpu
```

For `tiny_lpu` (RSI roadmap stage 5), run the same five-gate stack:

```powershell
python -m fpga_agent verify examples/agent_validation/tiny_lpu
python -m fpga_agent lint examples/agent_validation/tiny_lpu
python -m fpga_agent synth examples/agent_validation/tiny_lpu
python -m fpga_agent coverage examples/agent_validation/tiny_lpu
python -m fpga_agent cdc examples/agent_validation/tiny_lpu
```

For `tiny_train_gpu` (RSI roadmap stage 6), run the same five-gate stack. The host-side self-training loop is separate:

```powershell
python -m fpga_agent verify examples/agent_validation/tiny_train_gpu
python -m fpga_agent lint examples/agent_validation/tiny_train_gpu
python -m fpga_agent synth examples/agent_validation/tiny_train_gpu
python -m fpga_agent coverage examples/agent_validation/tiny_train_gpu
python -m fpga_agent cdc examples/agent_validation/tiny_train_gpu
python -m fpga_agent training
```

The `training` command trains a tiny ReLU linear model on the host (SGD), quantizes the weights to int32, and verifies the forward pass against the same 32-bit wraparound GEMM + ReLU math the `tiny_train_gpu` RTL implements.

For `tiny_soc` (RSI roadmap stage 7), run the same five-gate stack:

```powershell
python -m fpga_agent verify examples/agent_validation/tiny_soc
python -m fpga_agent lint examples/agent_validation/tiny_soc
python -m fpga_agent synth examples/agent_validation/tiny_soc
python -m fpga_agent coverage examples/agent_validation/tiny_soc
python -m fpga_agent cdc examples/agent_validation/tiny_soc
```

The bench is fixed before implementation work starts. A generated RTL pass is accepted only when the simulation report validates against the pinned bench and current source hashes. A synthesis pass does not imply board-level implementation or physical validation. Structural coverage (`icforge coverage`) measures line/branch/toggle/expr points; `--functional-coverage` additionally measures `cover property` points and treats a fired concurrent assertion as a failure — neither is assertion or specification coverage. `icforge timing` summarizes the backend's static timing signoff and reports post-route simulation as `blocked` when the target simulator is absent; `icforge release` aggregates all gates into a versioned release manifest without granting engineering signoff.
