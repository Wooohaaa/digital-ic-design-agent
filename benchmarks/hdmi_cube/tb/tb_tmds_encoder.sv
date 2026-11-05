`timescale 1ns/1ps

module tb_tmds_encoder;
    reg clk = 1'b0;
    reg reset = 1'b1;
    reg [7:0] data = 8'd0;
    reg de = 1'b0;
    reg c0 = 1'b0;
    reg c1 = 1'b0;
    wire [9:0] symbol;
    integer disparity;
    integer sample;
    integer bit_index;
    integer ones;
    integer balance;
    integer seed;
    reg use_xnor;
    reg [8:0] q_m;
    reg [9:0] expected;

    always #5 clk = ~clk;

    tmds_encoder dut (
        .clk(clk), .reset(reset), .data(data), .de(de), .c0(c0), .c1(c1),
        .symbol(symbol)
    );

    task check_sample;
        input [7:0] input_data;
        input input_de;
        input input_c0;
        input input_c1;
        begin
            @(negedge clk);
            data = input_data;
            de = input_de;
            c0 = input_c0;
            c1 = input_c1;

            if (!input_de) begin
                case ({input_c1, input_c0})
                    2'b00: expected = 10'b1101010100;
                    2'b01: expected = 10'b0010101011;
                    2'b10: expected = 10'b0101010100;
                    2'b11: expected = 10'b1010101011;
                endcase
                disparity = 0;
            end else begin
                ones = 0;
                for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1)
                    ones = ones + input_data[bit_index];
                use_xnor = (ones > 4) || ((ones == 4) && !input_data[0]);
                q_m = 9'd0;
                q_m[0] = input_data[0];
                for (bit_index = 1; bit_index < 8; bit_index = bit_index + 1) begin
                    if (use_xnor)
                        q_m[bit_index] = ~(q_m[bit_index - 1] ^ input_data[bit_index]);
                    else
                        q_m[bit_index] = q_m[bit_index - 1] ^ input_data[bit_index];
                end
                q_m[8] = !use_xnor;

                ones = 0;
                for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1)
                    ones = ones + q_m[bit_index];
                balance = 2 * ones - 8;

                if ((disparity == 0) || (balance == 0)) begin
                    if (q_m[8]) begin
                        expected = {2'b01, q_m[7:0]};
                        disparity = disparity + balance;
                    end else begin
                        expected = {2'b10, ~q_m[7:0]};
                        disparity = disparity - balance;
                    end
                end else if (((disparity > 0) && (balance > 0)) ||
                             ((disparity < 0) && (balance < 0))) begin
                    expected = {1'b1, q_m[8], ~q_m[7:0]};
                    disparity = disparity + (q_m[8] ? 2 : 0) - balance;
                end else begin
                    expected = {1'b0, q_m[8], q_m[7:0]};
                    disparity = disparity + balance - (q_m[8] ? 0 : 2);
                end
            end

            @(posedge clk);
            #1;
            if (symbol !== expected)
                $fatal(1, "TMDS mismatch for data=%02x de=%b c1c0=%b%b: got %010b expected %010b",
                       input_data, input_de, input_c1, input_c0, symbol, expected);
        end
    endtask

    initial begin
        disparity = 0;
        repeat (2) @(posedge clk);
        @(negedge clk);
        reset = 1'b0;

        check_sample(8'h00, 1'b0, 1'b0, 1'b0);
        check_sample(8'h00, 1'b0, 1'b1, 1'b0);
        check_sample(8'h00, 1'b0, 1'b0, 1'b1);
        check_sample(8'h00, 1'b0, 1'b1, 1'b1);

        for (sample = 0; sample < 256; sample = sample + 1)
            check_sample(sample[7:0], 1'b1, 1'b0, 1'b0);
        check_sample(8'h5a, 1'b0, 1'b0, 1'b1);
        for (sample = 255; sample >= 0; sample = sample - 1)
            check_sample(sample[7:0], 1'b1, 1'b0, 1'b0);

        seed = 32'h51f0a37d;
        for (sample = 0; sample < 512; sample = sample + 1) begin
            seed = (seed * 1664525) + 1013904223;
            check_sample(seed[15:8], 1'b1, 1'b0, 1'b0);
        end

        $display("PASS tb_tmds_encoder");
        $finish;
    end
endmodule
