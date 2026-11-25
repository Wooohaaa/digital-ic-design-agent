# Proof and observation boundary

Reset-state relation is explicitly selected in project.json. Every state bit
must use this reset, equal reset values establish each class, all members'
next-state values preserve it, and outputs agree for every related state.
The testbench checks asynchronous reset between rising edges, repeated reset,
enable/hold, and rollover. No board pin mapping or physical signoff is claimed.
