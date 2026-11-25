# RV32I independent execution comparison

The single-cycle, multicycle and two-stage pipeline cores run the same program images against the Unicorn RV32 engine. The adapter reads the model's PC and destination register after each step. It records byte writes through the engine's memory hook and converts them to the core testbench's address, byte-mask and lane-aligned data format. The comparison checks PC, instruction word, destination, value, ordered stores, final RAM and the core stop flag.

## Run locally

Install `iverilog` and `vvp` on PATH. From the repository root, run this opt-in comparison with the pinned Unicorn release.

```powershell
uv run --locked --with unicorn==2.1.4 python -m fpga_agent.cpu_external --seeds 64 --output reports/rv32-external
```

Use a new output directory for each run. The report links source and tool fingerprints to the captured program images, external reference results and actual Icarus logs. RTL and the trusted retirement testbench are copied into the run directory before compilation. Missing tooling returns status `blocked` with exit code 2. Execution or comparison errors return exit code 1, and success returns 0.

The default corpus contains 34 directed programs and 64 deterministic random seeds. Directed coverage includes signed and unsigned branches, jumps, immediate and register arithmetic, byte and halfword lanes, sign extension, word loads and stores, and FENCE. The negative tests alter a copied core's arithmetic right shift or byte store mask. The former must fail the retirement comparison and the latter must fail the ordered store comparison.

The fixture starts at PC 0 with text in the first page and data in `0x1000..0x10ff`. The terminal EBREAK is recorded as the fixture stop marker. Sixteen EEI trap cases continue to run under the existing Python benchmark. Architecture certification, privilege, misaligned trap behavior, and SoC behavior are outside this external comparison. The default project verification and LEC paths continue to produce their existing reports.

Engine and binary release details are available from [Unicorn Engine 2.1.4](https://github.com/unicorn-engine/unicorn/tree/2.1.4) and its [PyPI release](https://pypi.org/project/unicorn/2.1.4/).

Run the pinned upstream RV32I suite separately with the [ACT signature runner](rv32-act.md).

The [Stage 2 acceptance command](rv32-stage2.md) also checks terminal exceptions with native Unicorn observations and the frozen EEI policy.
