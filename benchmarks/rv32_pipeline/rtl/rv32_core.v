// Two-stage RV32I core: fetch into a one-entry instruction register, then
// execute/commit that instruction while fetching its sequential successor.
module rv32_core (
    input wire clk,
    input wire rst_n,
    output wire [31:0] imem_addr,
    input wire [31:0] imem_rdata,
    output wire dmem_valid,
    output wire [3:0] dmem_wstrb,
    output wire [31:0] dmem_addr,
    output wire [31:0] dmem_wdata,
    input wire [31:0] dmem_rdata,
    output reg retire_valid,
    output reg [31:0] retire_pc,
    output reg [31:0] retire_insn,
    output reg [4:0] retire_rd,
    output reg [31:0] retire_wdata,
    output reg halted,
    output reg trap
);

    reg [31:0] pc_fetch;
    reg [31:0] pc_ex;
    reg [31:0] insn_ex;
    reg ex_valid;
    reg [31:0] regs [0:31];
    integer i;

    wire [6:0] opcode = insn_ex[6:0];
    wire [4:0] rd = insn_ex[11:7];
    wire [2:0] funct3 = insn_ex[14:12];
    wire [4:0] rs1 = insn_ex[19:15];
    wire [4:0] rs2 = insn_ex[24:20];
    wire [6:0] funct7 = insn_ex[31:25];
    wire [31:0] rs1_value = (rs1 == 0) ? 32'b0 : regs[rs1];
    wire [31:0] rs2_value = (rs2 == 0) ? 32'b0 : regs[rs2];
    wire [31:0] imm_i = {{20{insn_ex[31]}}, insn_ex[31:20]};
    wire [31:0] imm_s = {{20{insn_ex[31]}}, insn_ex[31:25], insn_ex[11:7]};
    wire [31:0] imm_b = {{19{insn_ex[31]}}, insn_ex[31], insn_ex[7], insn_ex[30:25], insn_ex[11:8], 1'b0};
    wire [31:0] imm_u = {insn_ex[31:12], 12'b0};
    wire [31:0] imm_j = {{11{insn_ex[31]}}, insn_ex[31], insn_ex[19:12], insn_ex[20], insn_ex[30:21], 1'b0};

    reg [31:0] result;
    reg [31:0] effective_addr;
    reg [31:0] load_value;
    reg [31:0] store_value;
    reg [3:0] store_mask;
    reg is_store;
    reg is_load;
    reg writes_rd;
    reg legal;
    reg is_ebreak;
    reg is_ecall;
    reg branch_taken;
    reg [31:0] control_target;
    reg misaligned_data;
    reg misaligned_target;
    reg exception_now;
    reg [31:0] shifted_word;
    reg [7:0] byte_value;
    reg [15:0] half_value;
    reg [1:0] lane;

    assign imem_addr = pc_fetch;

    always @* begin
        result = 32'b0;
        effective_addr = 32'b0;
        load_value = 32'b0;
        store_value = 32'b0;
        store_mask = 4'b0;
        is_store = 1'b0;
        is_load = 1'b0;
        writes_rd = 1'b0;
        legal = 1'b0;
        is_ebreak = 1'b0;
        is_ecall = 1'b0;
        branch_taken = 1'b0;
        control_target = 32'b0;
        misaligned_data = 1'b0;
        misaligned_target = 1'b0;
        exception_now = 1'b0;
        shifted_word = 32'b0;
        byte_value = 8'b0;
        half_value = 16'b0;
        lane = 2'b0;

        case (opcode)
            7'b0110111: begin // LUI
                legal = 1'b1; writes_rd = 1'b1; result = imm_u;
            end
            7'b0010111: begin // AUIPC
                legal = 1'b1; writes_rd = 1'b1; result = pc_ex + imm_u;
            end
            7'b1101111: begin // JAL
                legal = 1'b1; writes_rd = 1'b1; result = pc_ex + 32'd4;
                control_target = pc_ex + imm_j;
                branch_taken = 1'b1;
            end
            7'b1100111: begin // JALR
                if (funct3 == 3'b000) begin
                    legal = 1'b1; writes_rd = 1'b1; result = pc_ex + 32'd4;
                    control_target = (rs1_value + imm_i) & 32'hffff_fffe;
                    branch_taken = 1'b1;
                end
            end
            7'b1100011: begin // conditional branches
                case (funct3)
                    3'b000: begin legal = 1'b1; branch_taken = (rs1_value == rs2_value); end
                    3'b001: begin legal = 1'b1; branch_taken = (rs1_value != rs2_value); end
                    3'b100: begin legal = 1'b1; branch_taken = ($signed(rs1_value) < $signed(rs2_value)); end
                    3'b101: begin legal = 1'b1; branch_taken = ($signed(rs1_value) >= $signed(rs2_value)); end
                    3'b110: begin legal = 1'b1; branch_taken = (rs1_value < rs2_value); end
                    3'b111: begin legal = 1'b1; branch_taken = (rs1_value >= rs2_value); end
                    default: begin end
                endcase
                control_target = pc_ex + imm_b;
            end
            7'b0010011: begin // OP-IMM
                case (funct3)
                    3'b000: begin legal = 1'b1; writes_rd = 1'b1; result = rs1_value + imm_i; end // ADDI
                    3'b010: begin legal = 1'b1; writes_rd = 1'b1; result = ($signed(rs1_value) < $signed(imm_i)) ? 32'd1 : 32'd0; end
                    3'b011: begin legal = 1'b1; writes_rd = 1'b1; result = (rs1_value < imm_i) ? 32'd1 : 32'd0; end
                    3'b100: begin legal = 1'b1; writes_rd = 1'b1; result = rs1_value ^ imm_i; end
                    3'b110: begin legal = 1'b1; writes_rd = 1'b1; result = rs1_value | imm_i; end
                    3'b111: begin legal = 1'b1; writes_rd = 1'b1; result = rs1_value & imm_i; end
                    3'b001: if (funct7 == 7'b0000000) begin legal = 1'b1; writes_rd = 1'b1; result = rs1_value << insn_ex[24:20]; end
                    3'b101: begin
                        if (funct7 == 7'b0000000) begin legal = 1'b1; writes_rd = 1'b1; result = rs1_value >> insn_ex[24:20]; end
                        else if (funct7 == 7'b0100000) begin legal = 1'b1; writes_rd = 1'b1; result = $signed(rs1_value) >>> insn_ex[24:20]; end
                    end
                    default: begin end
                endcase
            end
            7'b0110011: begin // OP
                case (funct3)
                    3'b000: begin
                        if (funct7 == 7'b0000000) begin legal = 1'b1; writes_rd = 1'b1; result = rs1_value + rs2_value; end
                        else if (funct7 == 7'b0100000) begin legal = 1'b1; writes_rd = 1'b1; result = rs1_value - rs2_value; end
                    end
                    3'b001: if (funct7 == 7'b0000000) begin legal = 1'b1; writes_rd = 1'b1; result = rs1_value << rs2_value[4:0]; end
                    3'b010: if (funct7 == 7'b0000000) begin legal = 1'b1; writes_rd = 1'b1; result = ($signed(rs1_value) < $signed(rs2_value)) ? 32'd1 : 32'd0; end
                    3'b011: if (funct7 == 7'b0000000) begin legal = 1'b1; writes_rd = 1'b1; result = (rs1_value < rs2_value) ? 32'd1 : 32'd0; end
                    3'b100: if (funct7 == 7'b0000000) begin legal = 1'b1; writes_rd = 1'b1; result = rs1_value ^ rs2_value; end
                    3'b101: begin
                        if (funct7 == 7'b0000000) begin legal = 1'b1; writes_rd = 1'b1; result = rs1_value >> rs2_value[4:0]; end
                        else if (funct7 == 7'b0100000) begin legal = 1'b1; writes_rd = 1'b1; result = $signed(rs1_value) >>> rs2_value[4:0]; end
                    end
                    3'b110: if (funct7 == 7'b0000000) begin legal = 1'b1; writes_rd = 1'b1; result = rs1_value | rs2_value; end
                    3'b111: if (funct7 == 7'b0000000) begin legal = 1'b1; writes_rd = 1'b1; result = rs1_value & rs2_value; end
                    default: begin end
                endcase
            end
            7'b0000011: begin // loads
                legal = 1'b1; is_load = 1'b1; writes_rd = 1'b1;
                effective_addr = rs1_value + imm_i;
                lane = effective_addr[1:0];
                shifted_word = dmem_rdata >> (lane * 8);
                byte_value = shifted_word[7:0];
                half_value = shifted_word[15:0];
                case (funct3)
                    3'b000: begin load_value = {{24{byte_value[7]}}, byte_value}; misaligned_data = 1'b0; end
                    3'b001: begin load_value = {{16{half_value[15]}}, half_value}; misaligned_data = effective_addr[0]; end
                    3'b010: begin load_value = dmem_rdata; misaligned_data = |effective_addr[1:0]; end
                    3'b100: begin load_value = {24'b0, byte_value}; misaligned_data = 1'b0; end
                    3'b101: begin load_value = {16'b0, half_value}; misaligned_data = effective_addr[0]; end
                    default: begin legal = 1'b0; is_load = 1'b0; writes_rd = 1'b0; end
                endcase
                result = load_value;
            end
            7'b0100011: begin // stores
                legal = 1'b1; is_store = 1'b1;
                effective_addr = rs1_value + imm_s;
                lane = effective_addr[1:0];
                case (funct3)
                    3'b000: begin store_mask = 4'b0001 << lane; store_value = rs2_value << (lane * 8); end
                    3'b001: begin store_mask = 4'b0011 << lane; store_value = rs2_value << (lane * 8); misaligned_data = effective_addr[0]; end
                    3'b010: begin store_mask = 4'b1111; store_value = rs2_value; misaligned_data = |effective_addr[1:0]; end
                    default: begin legal = 1'b0; is_store = 1'b0; end
                endcase
            end
            7'b0001111: begin // FENCE
                if (funct3 == 3'b000) legal = 1'b1;
            end
            7'b1110011: begin
                if (insn_ex == 32'h0000_0073) is_ecall = 1'b1;
                else if (insn_ex == 32'h0010_0073) begin legal = 1'b1; is_ebreak = 1'b1; end
            end
            default: begin end
        endcase

        if (branch_taken && control_target[1:0] != 2'b00)
            misaligned_target = 1'b1;
        exception_now = ex_valid && (!legal || is_ecall || misaligned_data || misaligned_target);
    end

    assign dmem_valid = rst_n && !halted && ex_valid && legal && is_store && !exception_now;
    assign dmem_addr = effective_addr;
    assign dmem_wstrb = dmem_valid ? store_mask : 4'b0;
    assign dmem_wdata = dmem_valid ? store_value : 32'b0;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pc_fetch <= 32'b0;
            pc_ex <= 32'b0;
            insn_ex <= 32'b0;
            ex_valid <= 1'b0;
            retire_valid <= 1'b0;
            retire_pc <= 32'b0;
            retire_insn <= 32'b0;
            retire_rd <= 5'b0;
            retire_wdata <= 32'b0;
            halted <= 1'b0;
            trap <= 1'b0;
            for (i = 0; i < 32; i = i + 1)
                regs[i] <= 32'b0;
        end else begin
            retire_valid <= 1'b0;
            retire_rd <= 5'b0;
            retire_wdata <= 32'b0;
            if (!halted) begin
                if (ex_valid) begin
                    if (exception_now) begin
                        halted <= 1'b1;
                        trap <= 1'b1;
                        ex_valid <= 1'b0;
                    end else begin
                        retire_valid <= 1'b1;
                        retire_pc <= pc_ex;
                        retire_insn <= insn_ex;
                        if (writes_rd && rd != 0) begin
                            regs[rd] <= result;
                            retire_rd <= rd;
                            retire_wdata <= result;
                        end
                        if (branch_taken) begin
                            pc_fetch <= control_target;
                            ex_valid <= 1'b0; // discard the fetched sequential instruction
                        end else begin
                            pc_ex <= pc_fetch;
                            insn_ex <= imem_rdata;
                            pc_fetch <= pc_fetch + 32'd4;
                            ex_valid <= 1'b1;
                        end
                        if (is_ebreak) begin
                            halted <= 1'b1;
                            ex_valid <= 1'b0;
                        end
                    end
                end else begin
                    pc_ex <= pc_fetch;
                    insn_ex <= imem_rdata;
                    pc_fetch <= pc_fetch + 32'd4;
                    ex_valid <= 1'b1;
                end
            end
            regs[0] <= 32'b0;
        end
    end
endmodule
