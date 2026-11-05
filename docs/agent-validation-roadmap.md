# IC Forge agent validation projects

These fixtures exercise three different design styles while pinning the host-authored acceptance harness. The coding agent may edit only `rtl/*.v`; it must not change the specification, testbench, manifest, or acceptance checks.

| Project | Engineering focus | Required verification |
|---|---|---|
| `sync_fifo` | State, pointer wrap, simultaneous operations, full/empty policy | Icarus self-checking scoreboard, deterministic directed and pseudo-random traffic, strict Verilator lint |
| `spi_master` | Parameterized serial timing and edge semantics | All four CPOL/CPHA modes, MSB-first TX/RX loopback model, multi-transfer checks, strict lint |
| `apb_pwm_timer` | APB setup/access timing, register side effects, timer/PWM/IRQ | Register read/write, invalid-address response, PWM boundary duty cases, interrupt set/clear checks, strict lint |

Each fixture has a trusted testbench SHA-256 pin. Simulation evidence is invalidated if an implementation agent or later edit changes the testbench. The local flow is Icarus simulation followed by Verilator `-Wall`; Anlogic TD synthesis is a separate synthesis-only gate because these blocks have no approved HX1P35A pin map. Full place-and-route, timing signoff, bitstream programming, and board behavior are not claimed by this suite.

## Host acceptance sequence

For each project, run:

```powershell
python -m fpga_agent verify examples/agent_validation/<project>
python -m fpga_agent lint examples/agent_validation/<project>
python -m fpga_agent review --scope simulation examples/agent_validation/<project>
python -m fpga_agent td --stage synth examples/agent_validation/<project>
```

The bench is fixed before implementation work starts. A generated RTL pass is accepted only when the simulation report validates against the pinned bench and current source hashes. A synthesis pass does not imply board-level implementation or physical validation.
