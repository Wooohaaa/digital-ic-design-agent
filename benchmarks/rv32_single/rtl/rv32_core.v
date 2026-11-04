module rv32_core (
    input  wire        clk,
    input  wire        rst_n,
    output wire [31:0] imem_addr,
    input  wire [31:0] imem_rdata,
    output wire        dmem_valid,
    output wire [3:0]  dmem_wstrb,
    output wire [31:0] dmem_addr,
    output wire [31:0] dmem_wdata,
    input  wire [31:0] dmem_rdata,
    output reg         retire_valid,
    output reg [31:0]  retire_pc,
    output reg [31:0]  retire_insn,
    output reg [4:0]   retire_rd,
    output reg [31:0]  retire_wdata,
    output reg         halted,
    output reg         trap
);

    reg [31:0] pc;
    reg [31:0] regs [0:31];

    wire [31:0] insn = imem_rdata;
    wire [6:0] opcode = insn[6:0];
    wire [4:0] rd = insn[11:7];
    wire [2:0] funct3 = insn[14:12];
    wire [4:0] rs1 = insn[19:15];
    wire [4:0] rs2 = insn[24:20];
    wire [6:0] funct7 = insn[31:25];
    wire [31:0] src1 = (rs1 == 0) ? 32'b0 : regs[rs1];
    wire [31:0] src2 = (rs2 == 0) ? 32'b0 : regs[rs2];

    wire [31:0] imm_i = {{20{insn[31]}}, insn[31:20]};
    wire [31:0] imm_s = {{20{insn[31]}}, insn[31:25], insn[11:7]};
    wire [31:0] imm_b = {{19{insn[31]}}, insn[31], insn[7], insn[30:25], insn[11:8], 1'b0};
    wire [31:0] imm_u = {insn[31:12], 12'b0};
    wire [31:0] imm_j = {{11{insn[31]}}, insn[31], insn[19:12], insn[20], insn[30:21], 1'b0};

    localparam [6:0] OP_LUI    = 7'b0110111;
    localparam [6:0] OP_AUIPC  = 7'b0010111;
    localparam [6:0] OP_JAL    = 7'b1101111;
    localparam [6:0] OP_JALR   = 7'b1100111;
    localparam [6:0] OP_BRANCH = 7'b1100011;
    localparam [6:0] OP_LOAD   = 7'b0000011;
    localparam [6:0] OP_STORE  = 7'b0100011;
    localparam [6:0] OP_IMM    = 7'b0010011;
    localparam [6:0] OP_REG    = 7'b0110011;
    localparam [6:0] OP_FENCE  = 7'b0001111;
    localparam [6:0] OP_SYSTEM = 7'b1110011;

    reg legal;
    reg is_load, is_store, is_branch, is_jump, is_ebreak, is_ecall;
    reg writes_rd;
    reg [31:0] result;
    reg [31:0] next_pc;
    reg branch_taken;
    reg [31:0] eff_addr;
    reg [1:0] mem_size;
    reg [31:0] shifted_load;
    reg [31:0] load_result;
    reg misaligned;
    reg [3:0] store_mask;
    reg [31:0] store_value;
    reg [31:0] alu_rhs;

    assign imem_addr = pc;
    assign dmem_addr = eff_addr;
    assign dmem_valid = rst_n && !halted && legal && !misaligned && (is_load || is_store);
    assign dmem_wstrb = (dmem_valid && is_store) ? store_mask : 4'b0000;
    assign dmem_wdata = store_value;

    always @* begin
        legal = 1'b0;
        is_load = 1'b0;
        is_store = 1'b0;
        is_branch = 1'b0;
        is_jump = 1'b0;
        is_ebreak = 1'b0;
        is_ecall = 1'b0;
        writes_rd = 1'b0;
        result = 32'b0;
        next_pc = pc + 32'd4;
        branch_taken = 1'b0;
        eff_addr = 32'b0;
        mem_size = 2'd2;
        shifted_load = 32'b0;
        load_result = 32'b0;
        misaligned = 1'b0;
        store_mask = 4'b0000;
        store_value = 32'b0;
        alu_rhs = 32'b0;

        case (opcode)
            OP_LUI: begin
                legal = 1'b1;
                writes_rd = 1'b1;
                result = imm_u;
            end
            OP_AUIPC: begin
                legal = 1'b1;
                writes_rd = 1'b1;
                result = pc + imm_u;
            end
            OP_JAL: begin
                legal = 1'b1;
                writes_rd = 1'b1;
                is_jump = 1'b1;
                result = pc + 32'd4;
                next_pc = pc + imm_j;
                if (next_pc[1:0] != 2'b00) misaligned = 1'b1;
            end
            OP_JALR: begin
                if (funct3 == 3'b000) begin
                    legal = 1'b1;
                    writes_rd = 1'b1;
                    is_jump = 1'b1;
                    result = pc + 32'd4;
                    next_pc = (src1 + imm_i) & 32'hffff_fffe;
                    if (next_pc[1:0] != 2'b00) misaligned = 1'b1;
                end
            end
            OP_BRANCH: begin
                is_branch = 1'b1;
                case (funct3)
                    3'b000: begin legal = 1'b1; branch_taken = (src1 == src2); end
                    3'b001: begin legal = 1'b1; branch_taken = (src1 != src2); end
                    3'b100: begin legal = 1'b1; branch_taken = ($signed(src1) < $signed(src2)); end
                    3'b101: begin legal = 1'b1; branch_taken = ($signed(src1) >= $signed(src2)); end
                    3'b110: begin legal = 1'b1; branch_taken = (src1 < src2); end
                    3'b111: begin legal = 1'b1; branch_taken = (src1 >= src2); end
                    default: begin end
                endcase
                if (branch_taken) begin
                    next_pc = pc + imm_b;
                    if (next_pc[1:0] != 2'b00) misaligned = 1'b1;
                end
            end
            OP_LOAD: begin
                is_load = 1'b1;
                eff_addr = src1 + imm_i;
                case (funct3)
                    3'b000, 3'b100: begin legal = 1'b1; mem_size = 2'd0; end
                    3'b001, 3'b101: begin legal = 1'b1; mem_size = 2'd1; end
                    3'b010: begin legal = 1'b1; mem_size = 2'd2; end
                    default: begin end
                endcase
                if ((mem_size == 2'd1 && eff_addr[0]) ||
                    (mem_size == 2'd2 && eff_addr[1:0] != 2'b00)) misaligned = 1'b1;
                shifted_load = dmem_rdata >> (8 * eff_addr[1:0]);
                case (funct3)
                    3'b000: load_result = {{24{shifted_load[7]}}, shifted_load[7:0]};
                    3'b001: load_result = {{16{shifted_load[15]}}, shifted_load[15:0]};
                    3'b010: load_result = dmem_rdata;
                    3'b100: load_result = {24'b0, shifted_load[7:0]};
                    3'b101: load_result = {16'b0, shifted_load[15:0]};
                    default: load_result = 32'b0;
                endcase
                writes_rd = legal;
                result = load_result;
            end
            OP_STORE: begin
                is_store = 1'b1;
                eff_addr = src1 + imm_s;
                case (funct3)
                    3'b000: begin legal = 1'b1; mem_size = 2'd0; end
                    3'b001: begin legal = 1'b1; mem_size = 2'd1; end
                    3'b010: begin legal = 1'b1; mem_size = 2'd2; end
                    default: begin end
                endcase
                if ((mem_size == 2'd1 && eff_addr[0]) ||
                    (mem_size == 2'd2 && eff_addr[1:0] != 2'b00)) misaligned = 1'b1;
                case (mem_size)
                    2'd0: store_mask = 4'b0001 << eff_addr[1:0];
                    2'd1: store_mask = 4'b0011 << eff_addr[1:0];
                    default: store_mask = 4'b1111;
                endcase
                store_value = src2 << (8 * eff_addr[1:0]);
            end
            OP_IMM: begin
                legal = 1'b1;
                writes_rd = 1'b1;
                case (funct3)
                    3'b000: result = src1 + imm_i;
                    3'b010: result = ($signed(src1) < $signed(imm_i)) ? 32'd1 : 32'd0;
                    3'b011: result = (src1 < imm_i) ? 32'd1 : 32'd0;
                    3'b100: result = src1 ^ imm_i;
                    3'b110: result = src1 | imm_i;
                    3'b111: result = src1 & imm_i;
                    3'b001: begin
                        if (funct7 == 7'b0000000) result = src1 << insn[24:20];
                        else legal = 1'b0;
                    end
                    3'b101: begin
                        if (funct7 == 7'b0000000) result = src1 >> insn[24:20];
                        else if (funct7 == 7'b0100000) result = $signed(src1) >>> insn[24:20];
                        else legal = 1'b0;
                    end
                    default: legal = 1'b0;
                endcase
                writes_rd = legal;
            end
            OP_REG: begin
                case (funct3)
                    3'b000: begin
                        if (funct7 == 7'b0000000) begin legal = 1'b1; result = src1 + src2; end
                        else if (funct7 == 7'b0100000) begin legal = 1'b1; result = src1 - src2; end
                    end
                    3'b001: begin if (funct7 == 7'b0000000) begin legal = 1'b1; result = src1 << src2[4:0]; end end
                    3'b010: begin if (funct7 == 7'b0000000) begin legal = 1'b1; result = ($signed(src1) < $signed(src2)) ? 32'd1 : 32'd0; end end
                    3'b011: begin if (funct7 == 7'b0000000) begin legal = 1'b1; result = (src1 < src2) ? 32'd1 : 32'd0; end end
                    3'b100: begin if (funct7 == 7'b0000000) begin legal = 1'b1; result = src1 ^ src2; end end
                    3'b101: begin
                        if (funct7 == 7'b0000000) begin legal = 1'b1; result = src1 >> src2[4:0]; end
                        else if (funct7 == 7'b0100000) begin legal = 1'b1; result = $signed(src1) >>> src2[4:0]; end
                    end
                    3'b110: begin if (funct7 == 7'b0000000) begin legal = 1'b1; result = src1 | src2; end end
                    3'b111: begin if (funct7 == 7'b0000000) begin legal = 1'b1; result = src1 & src2; end end
                    default: begin end
                endcase
                writes_rd = legal;
            end
            OP_FENCE: begin
                // RV32I base implementations treat reserved FENCE encodings as FENCE
                // and ignore fm, pred, succ, rs1, and rd. FENCE.I uses funct3=001.
                if (funct3 == 3'b000) legal = 1'b1;
            end
            OP_SYSTEM: begin
                if (insn == 32'h0000_0073) begin legal = 1'b1; is_ecall = 1'b1; end
                else if (insn == 32'h0010_0073) begin legal = 1'b1; is_ebreak = 1'b1; end
            end
            default: begin end
        endcase

        if (opcode == OP_LOAD || opcode == OP_STORE) begin
            // Illegal encodings and misaligned accesses are handled as synchronous traps.
            if (!legal) misaligned = 1'b0;
        end
    end

    integer i;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pc <= 32'b0;
            retire_valid <= 1'b0;
            retire_pc <= 32'b0;
            retire_insn <= 32'b0;
            retire_rd <= 5'b0;
            retire_wdata <= 32'b0;
            halted <= 1'b0;
            trap <= 1'b0;
            for (i = 0; i < 32; i = i + 1) regs[i] <= 32'b0;
        end else begin
            retire_valid <= 1'b0;
            if (!halted) begin
                if (!legal || misaligned || is_ecall) begin
                    halted <= 1'b1;
                    trap <= 1'b1;
                end else begin
                    retire_valid <= 1'b1;
                    retire_pc <= pc;
                    retire_insn <= insn;
                    if (writes_rd && rd != 0) begin
                        regs[rd] <= result;
                        retire_rd <= rd;
                        retire_wdata <= result;
                    end else begin
                        retire_rd <= 5'b0;
                        retire_wdata <= 32'b0;
                    end
                    if (is_ebreak) begin
                        halted <= 1'b1;
                        trap <= 1'b0;
                    end else begin
                        pc <= next_pc;
                    end
                end
            end
            regs[0] <= 32'b0;
        end
    end

endmodule
