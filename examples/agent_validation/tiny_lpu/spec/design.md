# tiny_lpu interface and behavior

Module: `tiny_lpu #(NMAC=4, NW=16, DW=32)`.

Ports: `clk`, synchronous active-low `rst_n`, `start`,
host weight port `wmem_wren`/`wmem_addr`/`wmem_wdata` (absolute address
`g*NW + k`, `wmem_wren` honored only when `!running`),
streaming input `act_valid`/`act_in`,
packed output `out[NMAC*DW-1:0]` (`out[g*DW +: DW]` is MAC g's accumulator),
and `done`.

## Execution model

The host loads `NMAC` banks of `NW` weights into on-chip SRAM. On a rising clock
edge with reset asserted, clear every accumulator, the shared index `idx`,
`running` and `done`. The engine stays idle until `start` is pulsed. While
running, each clock edge with `act_valid` asserted accumulates
`acc[g] += wmem[g][idx] * act_in` for every MAC `g` and advances `idx`; when
`idx` reaches `NW-1`, the engine halts and asserts `done`. `done` stays
asserted until the next `start` or reset. `wmem_wren` is only honored while the
engine is idle, so host loads cannot race execution.

## Behavior table

| signal | idle | running, act_valid | idx == NW-1 |
|---|---|---|---|
| acc[g] | holds | `acc[g] + wmem[g][idx]*act_in` | holds |
| idx | holds | `idx + 1` | holds (done) |
| running | 0 | 1 | 0 |
| done | 0 | 0 | 1 |

Arithmetic is `DW`-bit and wraps modulo 2^DW. No latches, no gated clocks, no
branching.
