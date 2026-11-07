# RTL mutation audit

IC Forge can measure whether a generic Icarus self-checking testbench catches a deterministic sample of common RTL operator faults. This is a testbench-strength diagnostic: simulation PASS by itself says only that the current design passed the current checks.

```powershell
icforge mutation-audit examples/agent_validation/sync_fifo
icforge mutation-audit examples/agent_validation/spi_master --max-mutants 256 --timeout 30 --total-timeout 3600
```

The command first requires the baseline verification to pass, then copies fingerprinted HDL/spec inputs to a disposable temporary project. It selects a deterministic, hash-ranked sample from the declared RTL using `--seed`, changes one supported operator at a time, and runs the same Icarus verifier. The original RTL, testbench, simulation report and build directory are left untouched. Progress is checkpointed to `reports/mutation.json`; rerunning the same source, seed, sample size and per-mutant timeout resumes completed mutants. Raising `--total-timeout` can extend the overall run without discarding that progress.

The report records the input fingerprints, simulator identity, exact file/line/operator for each mutant, outcome, elapsed time and bounded output excerpt. A complete report appears as the optional `DV-005` gate in `icforge review` and is included in normal project report archives.

## Interpret the outcomes

| Status | Meaning |
|---|---|
| `killed` | The testbench emitted a failure marker for the mutant. |
| `survived` | The mutant still passed; it may be equivalent or the testbench may miss the behavior. |
| `compile_error` | The textual mutation did not produce executable HDL; it is excluded from the kill-rate denominator. |
| `timeout` / `inconclusive` | The run did not produce decisive pass/fail evidence. |

The observed kill rate is `killed / (killed + survived)`. It is not a quality threshold, proof of equivalence, formal verification result, or engineering signoff. Survivors should be mapped to explicit requirements before changing a testbench. The command currently supports generic Icarus projects; RV32I, HDMI and TinyStories profiles retain their specialized verifiers.
