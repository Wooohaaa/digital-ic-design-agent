// Synthesizable SystemVerilog datapath used to pin the vendor-neutral flow's
// SystemVerilog support: always_ff/always_comb, typedef enum/struct, logic,
// parameters, $clog2 and generate-for/if. No vendor primitives.
module sv_datapath #(
    parameter int unsigned DATA_WIDTH = 8,
    parameter int unsigned DEPTH      = 4
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  load,
    input  logic                  start,
    input  logic                  shift_en,
    input  logic [DATA_WIDTH-1:0] din,
    output logic [DATA_WIDTH-1:0] sum,
    output logic [DATA_WIDTH-1:0] shift_out,
    output logic                  busy
);
    localparam int unsigned AW = (DEPTH > 1) ? $clog2(DEPTH) : 1;

    typedef enum logic [1:0] {S_IDLE, S_RUN, S_DONE} state_t;
    typedef struct packed {
        logic                  valid;
        logic [DATA_WIDTH-1:0] payload;
    } beat_t;

    state_t                state;
    beat_t                 beat;
    logic [DATA_WIDTH-1:0] accum;
    logic [AW-1:0]         index;
    logic [DATA_WIDTH-1:0] shift_reg [DEPTH];

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state        <= S_IDLE;
            beat.valid   <= 1'b0;
            beat.payload <= '0;
            accum        <= '0;
            index        <= '0;
            sum          <= '0;
        end else begin
            unique case (state)
                S_IDLE: begin
                    if (load) begin
                        beat.valid   <= 1'b1;
                        beat.payload <= din;
                    end
                    if (start && beat.valid) begin
                        state <= S_RUN;
                        accum <= '0;
                        index <= '0;
                    end
                end
                S_RUN: begin
                    accum <= accum + beat.payload;
                    if (index == AW'(DEPTH - 1)) begin
                        sum   <= accum + beat.payload;
                        state <= S_DONE;
                    end else begin
                        index <= index + 1'b1;
                    end
                end
                S_DONE: begin
                    state      <= S_IDLE;
                    beat.valid <= 1'b0;
                end
                default: state <= S_IDLE;
            endcase
        end
    end

    genvar g;
    generate
        for (g = 0; g < DEPTH; g++) begin : g_shift
            if (g == 0) begin : g_first
                always_ff @(posedge clk) begin
                    if (!rst_n)             shift_reg[g] <= '0;
                    else if (shift_en)      shift_reg[g] <= din;
                end
            end else begin : g_rest
                always_ff @(posedge clk) begin
                    if (!rst_n)             shift_reg[g] <= '0;
                    else if (shift_en)      shift_reg[g] <= shift_reg[g-1];
                end
            end
        end
    endgenerate

    always_comb busy = (state == S_RUN);
    assign shift_out = shift_reg[DEPTH-1];
endmodule
