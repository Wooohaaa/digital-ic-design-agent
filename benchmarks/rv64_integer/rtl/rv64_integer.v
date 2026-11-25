module rv64_integer #(
    parameter FULL_BASE = 0,
    parameter MACHINE = 0
) (
    input wire clk,
    input wire rst_n,
    input wire [31:0] instruction,
    input wire instruction_fault,
    input wire [63:0] data_read,
    input wire data_fault,
    output wire [63:0] fetch_pc,
    output reg retire_valid,
    output wire [63:0] retire_pc,
    output reg [31:0] retire_instruction,
    output reg [4:0] retire_rd,
    output reg [63:0] retire_value,
    output wire [63:0] data_address,
    output wire data_valid,
    output wire [7:0] data_write_mask,
    output wire [63:0] data_write_value,
    output reg halted,
    output reg trap
);
    // Four-byte alignment is structural, including before the first reset.
    reg [61:0] fetch_word, retire_word;
    assign fetch_pc = {fetch_word, 2'b0};
    assign retire_pc = {retire_word, 2'b0};
    reg [63:0] x [1:31];
    wire [6:0] opcode = instruction[6:0];
    wire [4:0] rd = instruction[11:7];
    wire [2:0] funct3 = instruction[14:12];
    wire [4:0] rs1 = instruction[19:15];
    wire [4:0] rs2 = instruction[24:20];
    wire [6:0] funct7 = instruction[31:25];
    wire [63:0] a = rs1 == 0 ? 64'b0 : x[rs1];
    wire [63:0] b = rs2 == 0 ? 64'b0 : x[rs2];
    wire [63:0] imm_i = {{52{instruction[31]}}, instruction[31:20]};
    wire [63:0] imm_u = {{32{instruction[31]}}, instruction[31:12], 12'b0};
    wire [63:0] imm_s = {{52{instruction[31]}}, instruction[31:25], instruction[11:7]};
    wire [63:0] imm_b = {{51{instruction[31]}}, instruction[31], instruction[7],
                         instruction[30:25], instruction[11:8], 1'b0};
    wire [63:0] imm_j = {{43{instruction[31]}}, instruction[31], instruction[19:12],
                         instruction[20], instruction[30:21], 1'b0};
    wire load = FULL_BASE && opcode == 7'h03;
    wire store = FULL_BASE && opcode == 7'h23;
    assign data_address = FULL_BASE ? a + (store ? imm_s : imm_i) : 64'b0;
    wire [2:0] lane = data_address[2:0];
    wire [1:0] size = funct3[1:0];
    wire [7:0] byte_mask = size == 0 ? 8'h01 :
                           size == 1 ? 8'h03 : size == 2 ? 8'h0f : 8'hff;
    wire bad_alignment = (lane & (size == 0 ? 3'd0 :
                                  size == 1 ? 3'd1 : size == 2 ? 3'd3 : 3'd7)) != 0;
    wire [63:0] read_lane = data_read >> {lane, 3'b0};
    wire [63:0] jalr_target = (a + imm_i) & ~64'd1;

    wire immediate = opcode == 7'h13 || opcode == 7'h1b;
    wire word_op = opcode == 7'h3b || opcode == 7'h1b;
    wire [63:0] operand = immediate ? imm_i : b;
    wire [5:0] shift = immediate ? instruction[25:20] : b[5:0];
    wire [31:0] a_word = a[31:0];
    wire [31:0] operand_word = operand[31:0];
    wire subtract = !immediate && funct7 == 7'h20;
    wire arithmetic = immediate && !word_op ? instruction[31:26] == 6'h10 : funct7 == 7'h20;
    wire normal_shift = immediate && !word_op ? instruction[31:26] == 0 : funct7 == 0;

    reg [63:0] value;
    reg [31:0] word_value;
    reg legal;
    reg writes_rd;
    reg [63:0] next_pc;
    reg [63:0] csr_read;
    reg csr_known;
    wire csr_instruction = MACHINE && opcode == 7'h73 && funct3 != 0 && funct3 != 4;
    wire csr_write = csr_instruction && (funct3[1:0] == 1 || rs1 != 0);
    wire [63:0] csr_operand = funct3[2] ? {59'b0, rs1} : a;
    wire [63:0] csr_next = funct3[1:0] == 1 ? csr_operand :
                           funct3[1:0] == 2 ? (csr_read | csr_operand) : (csr_read & ~csr_operand);
    wire mret = MACHINE && instruction == 32'h30200073;
    wire mie, mpie;
    wire [61:0] mtvec_word, mepc_word;
    wire [63:0] mscratch, mcause, mtval;
    always @* begin
        csr_known = 1;
        case (instruction[31:20])
            12'h300: csr_read = {51'b0, 2'b11, 3'b0, mpie, 3'b0, mie, 3'b0};
            12'h305: csr_read = {mtvec_word, 2'b0};
            12'h340: csr_read = mscratch;
            12'h341: csr_read = {mepc_word, 2'b0};
            12'h342: csr_read = mcause;
            12'h343: csr_read = mtval;
            default: begin csr_known = 0; csr_read = 0; end
        endcase
    end

    wire break_stop = instruction == 32'h00100073;
    wire fetch_error = FULL_BASE && instruction_fault;
    wire target_error = next_pc[1:0] != 0;
    wire memory_error = (load || store) && (bad_alignment || data_fault);
    wire exception = fetch_error || (!legal && !break_stop) || target_error || memory_error;
    assign data_valid = rst_n && !halted && !fetch_error && legal && (load || store) && !bad_alignment;
    assign data_write_mask = data_valid && store && !data_fault ? byte_mask << lane : 8'b0;
    assign data_write_value = FULL_BASE ? b << {lane, 3'b0} : 64'b0;

    generate
        if (MACHINE) begin : machine_state
            reg mie_bit, mpie_bit;
            reg [61:0] vector_word, exception_word;
            reg [63:0] scratch, cause, trap_value;
            assign {mie, mpie} = {mie_bit, mpie_bit};
            assign {mtvec_word, mepc_word} = {vector_word, exception_word};
            assign {mscratch, mcause, mtval} = {scratch, cause, trap_value};

            always @(posedge clk) begin
                if (!rst_n) begin
                    mie_bit <= 0;
                    mpie_bit <= 0;
                    vector_word <= 0;
                    exception_word <= 0;
                    scratch <= 0;
                    cause <= 0;
                    trap_value <= 0;
                end else if (!halted && exception) begin
                    exception_word <= fetch_word;
                    mpie_bit <= mie_bit;
                    mie_bit <= 0;
                    if (fetch_error) begin
                        cause <= 1;
                        trap_value <= fetch_pc;
                    end else if (!legal) begin
                        cause <= instruction == 32'h00000073 ? 64'd11 : 64'd2;
                        trap_value <= instruction == 32'h00000073 ? 64'd0 : {32'b0, instruction};
                    end else if (target_error) begin
                        cause <= 0;
                        trap_value <= next_pc;
                    end else begin
                        cause <= bad_alignment ? (store ? 64'd6 : 64'd4) : (store ? 64'd7 : 64'd5);
                        trap_value <= data_address;
                    end
                end else if (!halted && !break_stop) begin
                    if (mret) begin
                        mie_bit <= mpie_bit;
                        mpie_bit <= 1;
                    end else if (csr_write) begin
                        case (instruction[31:20])
                            12'h300: begin mie_bit <= csr_next[3]; mpie_bit <= csr_next[7]; end
                            12'h305: vector_word <= csr_next[63:2];
                            12'h340: scratch <= csr_next;
                            12'h341: exception_word <= csr_next[63:2];
                            12'h342: cause <= csr_next;
                            12'h343: trap_value <= csr_next;
                            default: ;
                        endcase
                    end
                end
            end
        end else begin : no_machine_state
            assign {mie, mpie, mtvec_word, mepc_word, mscratch, mcause, mtval} = 0;
        end
    endgenerate

    integer index;
    always @* begin
        value = 0;
        word_value = 0;
        legal = 1;
        writes_rd = 1;
        next_pc = fetch_pc + 64'd4;
        case (opcode)
            7'h37: value = imm_u;
            7'h17: value = fetch_pc + imm_u;
            7'h13, 7'h33, 7'h1b, 7'h3b: begin
                case (funct3)
                    3'b000: begin
                        legal = immediate || funct7 == 0 || subtract;
                        value = subtract ? a - operand : a + operand;
                        word_value = subtract ? a_word - operand_word : a_word + operand_word;
                    end
                    3'b001: begin
                        legal = normal_shift;
                        value = a << shift;
                        word_value = a_word << shift[4:0];
                    end
                    3'b101: begin
                        legal = normal_shift || arithmetic;
                        if (arithmetic) begin
                            value = $signed(a) >>> shift;
                            word_value = $signed(a_word) >>> shift[4:0];
                        end else begin
                            value = a >> shift;
                            word_value = a_word >> shift[4:0];
                        end
                    end
                    default: begin
                        legal = !word_op && (immediate || funct7 == 0);
                        case (funct3)
                            3'b010: value = {63'b0, $signed(a) < $signed(operand)};
                            3'b011: value = {63'b0, a < operand};
                            3'b100: value = a ^ operand;
                            3'b110: value = a | operand;
                            3'b111: value = a & operand;
                            default: value = 0;
                        endcase
                    end
                endcase
                if (word_op)
                    value = {{32{word_value[31]}}, word_value};
            end
            7'h6f: begin
                legal = FULL_BASE != 0;
                value = next_pc;
                next_pc = fetch_pc + imm_j;
            end
            7'h67: begin
                legal = FULL_BASE && funct3 == 0;
                value = next_pc;
                next_pc = jalr_target;
            end
            7'h63: begin
                legal = FULL_BASE && funct3 != 2 && funct3 != 3;
                writes_rd = 0;
                case (funct3)
                    0: if (a == b) next_pc = fetch_pc + imm_b;
                    1: if (a != b) next_pc = fetch_pc + imm_b;
                    4: if ($signed(a) < $signed(b)) next_pc = fetch_pc + imm_b;
                    5: if ($signed(a) >= $signed(b)) next_pc = fetch_pc + imm_b;
                    6: if (a < b) next_pc = fetch_pc + imm_b;
                    7: if (a >= b) next_pc = fetch_pc + imm_b;
                    default: ;
                endcase
            end
            7'h03: begin
                legal = load && funct3 != 7;
                case (funct3)
                    0: value = {{56{read_lane[7]}}, read_lane[7:0]};
                    1: value = {{48{read_lane[15]}}, read_lane[15:0]};
                    2: value = {{32{read_lane[31]}}, read_lane[31:0]};
                    3: value = read_lane;
                    4: value = {56'b0, read_lane[7:0]};
                    5: value = {48'b0, read_lane[15:0]};
                    6: value = {32'b0, read_lane[31:0]};
                    default: ;
                endcase
            end
            7'h23: begin
                legal = store && !funct3[2];
                writes_rd = 0;
            end
            7'h0f: begin
                legal = FULL_BASE && funct3 == 0;
                writes_rd = 0;
            end
            7'h73: begin
                legal = (csr_instruction && csr_known) || mret;
                writes_rd = !mret;
                if (mret)
                    next_pc = {mepc_word, 2'b0};
                else
                    value = MACHINE ? csr_read : 64'b0;
            end
            default: legal = 0;
        endcase
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            fetch_word <= 0;
            retire_valid <= 0;
            retire_word <= 0;
            retire_instruction <= 0;
            retire_rd <= 0;
            retire_value <= 0;
            halted <= 0;
            trap <= 0;
            for (index = 1; index < 32; index = index + 1)
                x[index] <= 0;
        end else begin
            retire_valid <= 0;
            if (!halted) begin
                trap <= 0;
                if (exception) begin
                    trap <= 1;
                    if (MACHINE)
                        fetch_word <= mtvec_word;
                    else
                        halted <= 1;
                end else if (break_stop) begin
                    halted <= 1;
                    retire_valid <= 1;
                    retire_word <= fetch_word;
                    retire_instruction <= instruction;
                    retire_rd <= 0;
                    retire_value <= 0;
                end else begin
                    retire_valid <= 1;
                    retire_word <= fetch_word;
                    retire_instruction <= instruction;
                    retire_rd <= writes_rd ? rd : 5'b0;
                    retire_value <= writes_rd && rd != 0 ? value : 64'b0;
                    fetch_word <= next_pc[63:2];
                    if (writes_rd && rd != 0)
                        x[rd] <= value;
                end
            end
        end
    end
endmodule
