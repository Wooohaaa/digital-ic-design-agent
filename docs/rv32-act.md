# RV32I architecture test signatures

The core runner accepts the 38 RV32I source files under `riscv-test-suite/rv32i_m/I/src` from [RISC-V ACT 2.7.4](https://github.com/riscv/riscv-arch-test/tree/96e3589a867955e94c95ba098822da54d3ae39c0). The lock file pins those sources and both upstream test headers by SHA-256. The runner compiles the original assembly with a small local model header and linker script.

## Run the matrix

Install Clang with its RISC-V target, LLD, llvm-nm and llvm-objcopy, plus Icarus Verilog. The LLVM tools should be in the same directory as Clang. From the repository root, obtain the upstream suite and run the comparison.

```powershell
git clone --depth 1 --branch 2.7.4 https://github.com/riscv/riscv-arch-test.git ../act-2.7.4
uv run --locked --with unicorn==2.1.4 python -m fpga_agent.cpu_act --suite ../act-2.7.4 --output reports/rv32-act
```

Use a new output directory for each run. Specify `--clang` when Clang is outside PATH. Repeat `--case` to run named tests such as `srai-01` or `jalr-01`. The complete run checks every signature word from all 38 cases on the single-cycle, multicycle and two-stage pipeline cores.

The test EEI starts execution at address zero, with read-only test text below `0x200000` and writable RAM below `0x210000`. The window accommodates the upstream branch and jump displacement tests. The HALT macro emits EBREAK at a dedicated symbol. The HDL harness requires retirement of that exact EBREAK without a trap before it reads the complete linked signature range. The model header leaves optional inline GPR diagnostics empty and uses the signature comparison for acceptance. Unicorn 2.1.4 runs the same ELF-derived image to the halt symbol and supplies the expected signature. Use the LLVM integrated assembler for the unmodified JALR case, whose `la x0` the tested GNU assembler rejects.

The report records upstream hashes, copied RTL and testbench artifacts, ELF symbols, initial test images, expected words and per-core logs. An assembler rejection or a signature mismatch fails the selected matrix. The test checks only this historical RV32I subtree and local EEI. ACT4 platform requirements, traps, privileged execution and certification remain outside its scope.
