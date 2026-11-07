# Agent evaluation loop

IC Forge already persists each long task's model decision, typed tool action, exit status, bounded output, acceptance target and delegation record. `task metrics` turns those traces into privacy-preserving process indicators that can guide the next harness change.

```powershell
icforge task metrics --workspace .
icforge task metrics --workspace . --limit 500
```

The report groups terminal tasks by provider, model, engineer/orchestrate mode and target. It includes host-gated completion rate, average decision steps and elapsed time, tool action failures and failed delegated jobs. It does not print objectives, prompts, local file paths, credentials or raw trace contents. Token and cost usage and human quality grading are not currently measured.

For a useful comparison, keep a small version-controlled workload fixed: FIFO wrap/full/empty behavior, all SPI CPOL/CPHA modes, APB register/timer behavior, and one RV32I core case. Hold specifications, testbenches, tool versions and acceptance target constant; change one model or orchestration setting at a time. Inspect representative failure logs before adjusting prompts or delegation policy. A completed result means its configured host acceptance gate passed, not that all engineering signoff or hardware gates passed.

The interactive TUI exposes the same summary with `/metrics`. The agent receives a compact outcome summary as context for future planning, while historical outcomes remain advisory and never replace current project fingerprints or verification evidence.
