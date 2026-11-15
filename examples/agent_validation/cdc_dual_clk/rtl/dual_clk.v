module dual_clk (
  input  logic clk_a,
  input  logic clk_b,
  input  logic rst_n,
  input  logic ping,
  output logic pong
);
  // Raw cross-domain transfer: `ping` is sampled in clk_a and consumed in
  // clk_b without a synchronizer. Stage 4's structural analysis reports the
  // `a_reg` crossing as a review candidate; it does not decide whether the
  // transfer is actually unsafe.
  logic a_reg;
  always_ff @(posedge clk_a or negedge rst_n) begin
    if (!rst_n) a_reg <= 1'b0;
    else a_reg <= ping;
  end
  always_ff @(posedge clk_b or negedge rst_n) begin
    if (!rst_n) pong <= 1'b0;
    else pong <= a_reg;
  end
endmodule
