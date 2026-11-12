module alu (
    input  logic [31:0] issue_pc,
    input  logic [31:0] issue_insn,
    input  logic [31:0] iq_rs1_value,
    input  logic [31:0] iq_rs2_value,
    output logic [31:0] alu_value,
    output logic [31:0] actual_next,
    output logic [31:0] taken_target,
    output logic        taken
);
    logic [6:0] opcode;
    logic [2:0] funct3;
    logic [6:0] funct7;
    logic [31:0] imm_i;
    logic [31:0] imm_s;
    logic [31:0] imm_b;
    logic [31:0] imm_u;
    logic [31:0] imm_j;
    logic [31:0] op_a;
    logic [31:0] op_b;
    logic [31:0] result;
    logic [31:0] br_target;
    logic [31:0] jal_target;
    logic [31:0] jalr_target;
    logic        br_taken;
    logic        is_branch;
    logic        is_jal;
    logic        is_jalr;
    logic        is_alu;
    logic        is_lui;
    logic        is_auipc;
    logic        is_load;
    logic        is_store;
    logic        is_ecall;
    logic [4:0] shamt;
    logic [31:0] sra_res;
    logic [31:0] srl_res;

    assign opcode = issue_insn[6:0];
    assign funct3 = issue_insn[14:12];
    assign funct7 = issue_insn[31:25];
    assign imm_i  = {{20{issue_insn[31]}}, issue_insn[31:20]};
    assign imm_s  = {{20{issue_insn[31]}}, issue_insn[31:25], issue_insn[11:7]};
    assign imm_b  = {{19{issue_insn[31]}}, issue_insn[31], issue_insn[7],
                     issue_insn[30:25], issue_insn[11:8], 1'b0};
    assign imm_u  = {issue_insn[31:12], 12'b0};
    assign imm_j  = {{11{issue_insn[31]}}, issue_insn[31], issue_insn[19:12],
                     issue_insn[20], issue_insn[30:21], 1'b0};

    assign is_branch = (opcode == 7'b1100011);
    assign is_jal    = (opcode == 7'b1101111);
    assign is_jalr   = (opcode == 7'b1100111);
    assign is_alu    = (opcode == 7'b0110011) || (opcode == 7'b0010011);
    assign is_lui    = (opcode == 7'b0110111);
    assign is_auipc  = (opcode == 7'b0010111);
    assign is_load   = (opcode == 7'b0000011);
    assign is_store  = (opcode == 7'b0100011);
    assign is_ecall  = (issue_insn == 32'h0000_0073);

    assign op_a = iq_rs1_value;
    assign shamt = (opcode == 7'b0110011) ? iq_rs2_value[4:0] : issue_insn[24:20];

    always_comb begin
        op_b = 32'b0;
        if (opcode == 7'b0110011) begin
            op_b = iq_rs2_value;
        end else if (opcode == 7'b0010011) begin
            op_b = imm_i;
        end else if (is_load || is_store) begin
            op_b = imm_i;
        end else if (is_jalr) begin
            op_b = imm_i;
        end
    end

    assign srl_res = op_a >> shamt;
    assign sra_res = $signed(op_a) >>> shamt;

    always_comb begin
        result = 32'b0;
        if (opcode == 7'b0110011) begin
            case (funct3)
                3'b000: result = funct7[5] ? (op_a - op_b) : (op_a + op_b);
                3'b001: result = op_a << shamt;
                3'b010: result = ($signed(op_a) < $signed(op_b)) ? 32'd1 : 32'd0;
                3'b011: result = (op_a < op_b) ? 32'd1 : 32'd0;
                3'b100: result = op_a ^ op_b;
                3'b101: result = funct7[5] ? sra_res : srl_res;
                3'b110: result = op_a | op_b;
                3'b111: result = op_a & op_b;
                default: result = 32'b0;
            endcase
        end else if (opcode == 7'b0010011) begin
            case (funct3)
                3'b000: result = op_a + imm_i;
                3'b001: result = op_a << shamt;
                3'b010: result = ($signed(op_a) < $signed(imm_i)) ? 32'd1 : 32'd0;
                3'b011: result = (op_a < imm_i) ? 32'd1 : 32'd0;
                3'b100: result = op_a ^ imm_i;
                3'b101: result = funct7[5] ? sra_res : srl_res;
                3'b110: result = op_a | imm_i;
                3'b111: result = op_a & imm_i;
                default: result = 32'b0;
            endcase
        end else if (is_lui) begin
            result = imm_u;
        end else if (is_auipc) begin
            result = issue_pc + imm_u;
        end else if (is_jal || is_jalr) begin
            result = issue_pc + 32'd4;
        end else if (is_load || is_store) begin
            result = op_a + imm_i;
        end else begin
            result = 32'b0;
        end
    end

    assign br_target   = issue_pc + imm_b;
    assign jal_target  = issue_pc + imm_j;
    assign jalr_target = (iq_rs1_value + imm_i) & 32'hFFFF_FFFE;

    always_comb begin
        br_taken = 1'b0;
        if (is_branch) begin
            case (funct3)
                3'b000: br_taken = (iq_rs1_value == iq_rs2_value);
                3'b001: br_taken = (iq_rs1_value != iq_rs2_value);
                3'b100: br_taken = ($signed(iq_rs1_value) < $signed(iq_rs2_value));
                3'b101: br_taken = ($signed(iq_rs1_value) >= $signed(iq_rs2_value));
                3'b110: br_taken = (iq_rs1_value < iq_rs2_value);
                3'b111: br_taken = (iq_rs1_value >= iq_rs2_value);
                default: br_taken = 1'b0;
            endcase
        end
    end

    always_comb begin
        taken = 1'b0;
        taken_target = 32'b0;
        if (is_branch) begin
            taken = br_taken;
            taken_target = br_target;
        end else if (is_jal) begin
            taken = 1'b1;
            taken_target = jal_target;
        end else if (is_jalr) begin
            taken = 1'b1;
            taken_target = jalr_target;
        end
    end

    always_comb begin
        actual_next = issue_pc + 32'd4;
        if (taken) begin
            actual_next = taken_target;
        end
    end

    assign alu_value = result;
endmodule
