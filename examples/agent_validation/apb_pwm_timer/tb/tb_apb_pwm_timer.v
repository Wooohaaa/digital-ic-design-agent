`timescale 1ns/1ps
module tb_apb_pwm_timer;
    reg pclk = 0;
    always #5 pclk = ~pclk;
    reg presetn = 0;
    reg psel = 0, penable = 0, pwrite = 0;
    reg [7:0] paddr = 0;
    reg [31:0] pwdata = 0;
    wire [31:0] prdata;
    wire pready, pslverr, pwm_out, irq;
    apb_pwm_timer dut(.*);

    reg [31:0] model_period = 8;
    reg [31:0] model_duty = 4;
    reg [1:0] model_control = 0;
    reg model_pending = 0;
    reg [31:0] model_count = 0;
    integer j;
    integer highs;

    always @(posedge pclk) begin
        if (!presetn) begin
            model_period = 8;
            model_duty = 4;
            model_control = 0;
            model_pending = 0;
            model_count = 0;
        end else begin
            if (model_control[0]) begin
                if (model_count >= model_period - 1) begin
                    model_count = 0;
                    model_pending = 1;
                end else model_count = model_count + 1;
            end
            if (psel && penable && pready && pwrite && !pslverr) begin
                case (paddr)
                    8'h00: model_period = (pwdata < 2) ? 2 : pwdata;
                    8'h04: model_duty = pwdata;
                    8'h08: model_control = pwdata[1:0];
                    8'h0c: if (pwdata[0]) model_pending = 0;
                    default: begin end
                endcase
            end
        end
        #1;
        if (pwm_out !== (model_control[0] && model_count < model_duty))
            $fatal(1, "PWM cycle mismatch: count=%0d period=%0d duty=%0d enabled=%b got=%b",
                   model_count, model_period, model_duty, model_control[0], pwm_out);
        if (irq !== (model_control[1] && model_pending))
            $fatal(1, "IRQ mismatch: control=%b pending=%b got=%b", model_control, model_pending, irq);
    end

    task automatic apb_write(input [7:0] address, input [31:0] data, input reg expect_error);
        begin
            @(negedge pclk);
            psel = 1; penable = 0; pwrite = 1; paddr = address; pwdata = data;
            @(negedge pclk);
            penable = 1;
            @(posedge pclk);
            #1;
            if (pready !== 1'b1) $fatal(1, "PREADY did not complete access");
            if (pslverr !== expect_error) $fatal(1, "PSLVERR mismatch at write address %02x", address);
            @(negedge pclk);
            psel = 0; penable = 0; pwrite = 0;
        end
    endtask

    task automatic apb_read(input [7:0] address, input [31:0] expected, input reg expect_error);
        begin
            @(negedge pclk);
            psel = 1; penable = 0; pwrite = 0; paddr = address; pwdata = 0;
            @(negedge pclk);
            penable = 1;
            #1;
            if (pslverr !== expect_error) $fatal(1, "PSLVERR mismatch at read address %02x", address);
            if (!expect_error && prdata !== expected)
                $fatal(1, "APB read %02x got %08x expected %08x", address, prdata, expected);
            @(posedge pclk);
            #1;
            @(negedge pclk);
            psel = 0; penable = 0;
        end
    endtask

    task automatic measure_pwm(input integer cycles, input integer expected_highs);
        begin
            highs = 0;
            for (j = 0; j < cycles; j = j + 1) begin
                @(negedge pclk);
                #1;
                if (pwm_out) highs = highs + 1;
            end
            if (highs != expected_highs)
                $fatal(1, "PWM high cycles mismatch: got %0d expected %0d over %0d clocks",
                       highs, expected_highs, cycles);
        end
    endtask

    initial begin
        repeat (3) @(posedge pclk);
        #1;
        if (pready !== 1'b1 || pwm_out !== 1'b0 || irq !== 1'b0) $fatal(1, "reset/idle outputs incorrect");
        presetn = 1;
        apb_read(8'h00, 8, 0);
        apb_read(8'h04, 4, 0);
        apb_read(8'h08, 0, 0);
        apb_read(8'h0c, 0, 0);

        apb_write(8'h00, 8, 0);
        apb_write(8'h04, 3, 0);
        apb_write(8'h08, 3, 0);
        measure_pwm(16, 6);
        apb_read(8'h0c, 1, 0);
        if (irq !== 1'b1) $fatal(1, "enabled timer did not assert IRQ after rollover");
        apb_write(8'h0c, 1, 0);
        apb_read(8'h0c, 0, 0);
        if (irq !== 1'b0) $fatal(1, "W1C did not clear IRQ");

        apb_write(8'h08, 0, 0);
        measure_pwm(8, 0);
        apb_write(8'h04, 0, 0);
        apb_write(8'h08, 1, 0);
        measure_pwm(16, 0);
        apb_read(8'h0c, 1, 0);
        if (irq !== 1'b0) $fatal(1, "IRQ enable bit did not gate the pending interrupt");
        apb_write(8'h08, 3, 0);
        if (irq !== 1'b1) $fatal(1, "enabling IRQ did not expose pending status");
        apb_write(8'h0c, 1, 0);

        apb_write(8'h08, 0, 0);
        apb_write(8'h00, 2, 0);
        apb_write(8'h04, 2, 0);
        apb_write(8'h08, 1, 0);
        measure_pwm(8, 8);
        apb_write(8'h04, 1, 0);
        measure_pwm(4, 2);

        apb_write(8'h10, 32'hdeadbeef, 1);
        apb_read(8'h10, 0, 1);
        apb_write(8'h08, 0, 0);
        apb_read(8'h00, 2, 0);
        apb_read(8'h04, 1, 0);

        $display("CHECK REQ-001 PASS");
        $display("CHECK REQ-002 PASS");
        $display("CHECK REQ-003 PASS");
        $display("CHECK REQ-004 PASS");
        $display("CHECK REQ-005 PASS");
        $display("PASS: apb_pwm_timer APB, PWM, and interrupt checks");
        $finish;
    end
    initial begin
        #30000;
        $fatal(1, "APB PWM testbench timeout");
    end
endmodule
