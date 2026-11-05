module apb_pwm_timer (
    input wire pclk,
    input wire presetn,
    input wire psel,
    input wire penable,
    input wire pwrite,
    input wire [7:0] paddr,
    input wire [31:0] pwdata,
    output wire [31:0] prdata,
    output wire pready,
    output wire pslverr,
    output wire pwm_out,
    output wire irq
);
    // TODO: Implement APB register/timer behavior from spec/design.md.
    assign prdata = 32'b0;
    assign pready = 1'b1;
    assign pslverr = 1'b0;
    assign pwm_out = 1'b0;
    assign irq = 1'b0;
endmodule
