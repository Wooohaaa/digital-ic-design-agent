# tiny_train_gpu interface and behavior

Module: `tiny_train_gpu #(N=4, DW=32)`.

Ports: `clk`, synchronous active-low `rst_n`, `start`, `raw`,
host matrix-A port `a_wren`/`a_addr`/`a_wdata` (row-major, address `i*N + k`),
host matrix-B port `b_wren`/`b_addr`/`b_wdata` (row-major, address `k*N + j`),
host result port `c_rden`/`c_addr`/`c_rdata` (address `i*N + j`),
and `done`.

## Execution model

On reset clear the `i`, `j`, `k` counters, the accumulator `acc`, `running` and
`done`. While idle, the host may write A and B (`a_wren`/`b_wren` are honored
only when `!running`) and read C. On `start`, the engine runs a triple-nested
loop over `k` (inner), `j` (middle), `i` (outer): each cycle accumulates
`acc += A[i][k] * B[k][j]`; when `k` reaches `N-1` it stores the result to
`C[i][j]`, resets `acc`, and advances `j`/`i`. After the last element the engine
halts and asserts `done`, which stays asserted until the next `start` or reset.

## Activation

`raw=0` applies ReLU: a sum whose 32-bit pattern is signed-negative stores 0.
`raw=1` stores the wrapped 32-bit sum directly (bypass activation). Arithmetic
is `DW`-bit and wraps modulo 2^DW. No latches, no gated clocks.

| condition | action |
|---|---|
| `!rst_n` | clear counters/acc/running/done |
| `start && !running` | restart the loop from (0,0,0), clear acc/done |
| running, `k < N-1` | `acc += A[i][k]*B[k][j]`, `k++` |
| running, `k == N-1` | `C[i][j] = raw ? acc : ReLU(acc)`, advance j/i or done |
