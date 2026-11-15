`timescale 1ns/1ps

module hx1p35a_heartbeat #(
    parameter [31:0] HALF_PERIOD_CYCLES = 32'd25000000
) (
    input  wire I_sys_clk,
    input  wire I_rst_n,
    output reg  O_screen_pwm
);

    reg [31:0] cycle_count;

    always @(posedge I_sys_clk or negedge I_rst_n) begin
        if (!I_rst_n) begin
            cycle_count  <= 32'd0;
            O_screen_pwm <= 1'b0;
        end else if (cycle_count == HALF_PERIOD_CYCLES - 1'b1) begin
            cycle_count  <= 32'd0;
            O_screen_pwm <= ~O_screen_pwm;
        end else begin
            cycle_count <= cycle_count + 1'b1;
        end
    end

endmodule
