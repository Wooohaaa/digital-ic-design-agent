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
    localparam [7:0] ADDR_PERIOD  = 8'h00;
    localparam [7:0] ADDR_DUTY    = 8'h04;
    localparam [7:0] ADDR_CONTROL = 8'h08;
    localparam [7:0] ADDR_STATUS  = 8'h0c;

    reg [31:0] period_reg;
    reg [31:0] duty_reg;
    reg [1:0] control_reg;
    reg status_pending;
    reg [31:0] counter;

    wire valid_address;
    wire timer_wrap;

    assign valid_address = (paddr == ADDR_PERIOD) ||
                           (paddr == ADDR_DUTY) ||
                           (paddr == ADDR_CONTROL) ||
                           (paddr == ADDR_STATUS);
    assign timer_wrap = control_reg[0] && (counter >= (period_reg - 32'd1));

    assign pready = 1'b1;
    assign pslverr = psel && penable && !valid_address;

    assign prdata = (psel && penable && !pwrite) ?
                    ((paddr == ADDR_PERIOD)  ? period_reg :
                     (paddr == ADDR_DUTY)    ? duty_reg :
                     (paddr == ADDR_CONTROL) ? {30'b0, control_reg} :
                     (paddr == ADDR_STATUS)  ? {31'b0, status_pending} :
                     32'b0) : 32'b0;

    assign pwm_out = control_reg[0] && (counter < duty_reg);
    assign irq = control_reg[1] && status_pending;

    always @(posedge pclk) begin
        if (!presetn) begin
            period_reg <= 32'd8;
            duty_reg <= 32'd4;
            control_reg <= 2'b00;
            status_pending <= 1'b0;
            counter <= 32'd0;
        end else begin
            if (control_reg[0]) begin
                if (timer_wrap) begin
                    counter <= 32'd0;
                    status_pending <= 1'b1;
                end else begin
                    counter <= counter + 32'd1;
                end
            end

            if (psel && penable && pwrite && !pslverr) begin
                case (paddr)
                    ADDR_PERIOD: begin
                        period_reg <= (pwdata < 32'd2) ? 32'd2 : pwdata;
                    end
                    ADDR_DUTY: begin
                        duty_reg <= pwdata;
                    end
                    ADDR_CONTROL: begin
                        control_reg <= pwdata[1:0];
                    end
                    ADDR_STATUS: begin
                        if (pwdata[0] && !timer_wrap)
                            status_pending <= 1'b0;
                    end
                    default: begin
                    end
                endcase
            end
        end
    end
endmodule
