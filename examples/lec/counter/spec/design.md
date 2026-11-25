# Proof contract

Single positive-edge primary clock; synchronous active-high reset. Initial
register values are unconstrained Boolean values. LEC must check equal initial
state sets, complete state mapping, all next-state bits, and all observable
outputs. No reset sequence, zero initialization, or reachable-state assumption
may be silently added to make the proof pass.
