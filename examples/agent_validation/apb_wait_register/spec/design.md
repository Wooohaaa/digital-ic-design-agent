# APB wait-state register contract

| Byte address | Read | Write |
| --- | --- | --- |
| 0x00 | Stored byte | Replace the byte and increment the write count modulo 256 |
| 0x04 | Write count | Complete with an error and no side effect |
| Other | Complete with an error and return zero | Complete with an error and no side effect |

A rising edge with PSEL high and PENABLE low captures setup fields. With PSEL and PENABLE high, the first access cycle drives PREADY low and the next access cycle drives it high. A write commits only at the rising edge ending that ready cycle. Read data and PSLVERR are driven only during completion, with zero returned during writes and errors.

PSEL may stay high for consecutive transfers. A fresh setup edge is required for each one. An access without setup, continued access after completion, or a deselected transfer cannot commit. A new setup replaces any pending request. If address, direction or payload changes during access, the captured setup fields still determine the result. These choices define fixture behavior for input sequences outside the APB requester requirements.

Active-low reset clears the register, count and pending transaction on the rising clock edge. The reset input also masks all response outputs while asserted. Synthesis uses generic cells and the project has no approved board pin assignments.
