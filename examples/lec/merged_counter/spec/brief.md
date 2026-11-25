# Explicit post-reset merged-state fixture

After asserting active-low asynchronous rst_n, count is an enabled 8-bit
counter; low always equals count[1:0]. The output is {count,low}. Synthesis
can merge low into count's low bits. Before reset the independent registers
have unconstrained initial values, so arbitrary-initial-state bijection must
not be silently substituted with this post-reset proof.
