module apb_wait_reference (
    input  wire       clk, rst_n, psel, penable, pwrite,
    input  wire [7:0] paddr, pwdata,
    output wire       relation_ok, ports_ok,
    output wire       wait_pending, error_response,
    output reg  [7:0] accepted_writes
);
    wire [7:0] prdata, dut_addr, dut_data, dut_value, dut_count;
    wire pready, pslverr, dut_write;
    wire [1:0] dut_phase;
    apb_wait_register dut (
        .clk(clk), .rst_n(rst_n), .psel(psel), .penable(penable),
        .pwrite(pwrite), .paddr(paddr), .pwdata(pwdata), .prdata(prdata),
        .pready(pready), .pslverr(pslverr), .phase(dut_phase),
        .setup_write(dut_write), .setup_addr(dut_addr), .setup_data(dut_data),
        .value(dut_value), .write_count(dut_count)
    );

    // Track whether the immediately preceding edges contained setup and wait.
    reg previous_setup, previous_wait;
    reg [16:0] request;
    reg [7:0] stored;
    wire setup_sample = psel && !penable;
    wire access_sample = psel && penable;
    wire responds = rst_n && access_sample && previous_wait;
    reg [8:0] reply;
    reg changes_register;
    always @* begin
        reply = 9'h000;
        changes_register = 1'b0;
        if (responds) begin
            case (request[16:8])
                9'h000: reply = {1'b0, stored};
                9'h004: reply = {1'b0, accepted_writes};
                9'h100: changes_register = 1'b1;
                default: reply = 9'h100;
            endcase
        end
    end

    always @(posedge clk) begin
        previous_setup <= rst_n && setup_sample;
        previous_wait <= rst_n && previous_setup && access_sample;
        if (!rst_n) begin
            request <= 17'h00000;
            stored <= 8'h00;
            accepted_writes <= 8'h00;
        end else begin
            if (setup_sample)
                request <= {pwrite, paddr, pwdata};
            if (changes_register) begin
                stored <= request[7:0];
                accepted_writes <= accepted_writes + 8'd1;
            end
        end
    end

    assign wait_pending = rst_n && access_sample && previous_setup;
    assign error_response = reply[8];
    assign relation_ok = !(previous_wait && previous_setup) &&
                         ({previous_wait, previous_setup} === dut_phase) &&
                         (request === {dut_write, dut_addr, dut_data}) &&
                         (stored === dut_value) && (accepted_writes === dut_count);
    assign ports_ok = {responds, reply} === {pready, pslverr, prdata};
endmodule
