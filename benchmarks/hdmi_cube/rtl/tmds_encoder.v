module tmds_encoder(
    input wire clk,
    input wire reset,
    input wire [7:0] data,
    input wire de,
    input wire c0,
    input wire c1,
    output reg [9:0] symbol
);

    reg [8:0] transition_minimized;
    reg use_xor;
    reg [3:0] data_ones;
    reg [3:0] symbol_ones;
    reg signed [5:0] symbol_balance;
    reg signed [5:0] running_disparity;
    integer bit_index;

    always @* begin
        data_ones = 4'd0;
        for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1)
            data_ones = data_ones + data[bit_index];

        use_xor = (data_ones < 4'd4) ||
                  ((data_ones == 4'd4) && data[0]);

        transition_minimized = 9'd0;
        transition_minimized[0] = data[0];
        for (bit_index = 1; bit_index < 8; bit_index = bit_index + 1) begin
            if (use_xor)
                transition_minimized[bit_index] =
                    transition_minimized[bit_index - 1] ^ data[bit_index];
            else
                transition_minimized[bit_index] =
                    ~(transition_minimized[bit_index - 1] ^ data[bit_index]);
        end
        transition_minimized[8] = use_xor;

        symbol_ones = 4'd0;
        for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1)
            symbol_ones = symbol_ones + transition_minimized[bit_index];

        symbol_balance = $signed({1'b0, symbol_ones, 1'b0}) - 6'sd8;
    end

    always @(posedge clk) begin
        if (reset) begin
            symbol <= 10'b0;
            running_disparity <= 6'sd0;
        end else if (!de) begin
            case ({c1, c0})
                2'b00: symbol <= 10'b1101010100;
                2'b01: symbol <= 10'b0010101011;
                2'b10: symbol <= 10'b0101010100;
                2'b11: symbol <= 10'b1010101011;
            endcase
            running_disparity <= 6'sd0;
        end else if ((running_disparity == 0) || (symbol_balance == 0)) begin
            symbol[9] <= ~transition_minimized[8];
            symbol[8] <= transition_minimized[8];
            if (transition_minimized[8])
                symbol[7:0] <= transition_minimized[7:0];
            else
                symbol[7:0] <= ~transition_minimized[7:0];

            if (transition_minimized[8])
                running_disparity <= running_disparity + symbol_balance;
            else
                running_disparity <= running_disparity - symbol_balance;
        end else if (((running_disparity > 0) && (symbol_balance > 0)) ||
                     ((running_disparity < 0) && (symbol_balance < 0))) begin
            symbol <= {1'b1, transition_minimized[8],
                       ~transition_minimized[7:0]};
            if (transition_minimized[8])
                running_disparity <= running_disparity + 6'sd2 - symbol_balance;
            else
                running_disparity <= running_disparity - symbol_balance;
        end else begin
            symbol <= {1'b0, transition_minimized[8],
                       transition_minimized[7:0]};
            if (transition_minimized[8])
                running_disparity <= running_disparity + symbol_balance;
            else
                running_disparity <= running_disparity + symbol_balance - 6'sd2;
        end
    end

endmodule
