module alu (
    input logic [31:0] issue_pc, issue_insn, iq_rs1_value, iq_rs2_value,
    output logic [31:0] alu_value, actual_next, taken_target,
    output logic taken
);
    localparam [6:0] OP_REG=7'h33, OP_IMM=7'h13, OP_LUI=7'h37, OP_AUIPC=7'h17,
        OP_LOAD=7'h03, OP_STORE=7'h23, OP_BRANCH=7'h63, OP_JAL=7'h6f, OP_JALR=7'h67;
    logic [31:0] imm_i, imm_b, imm_j, alu_rhs;

    assign imm_i = {{20{issue_insn[31]}}, issue_insn[31:20]};
    assign imm_b = {{19{issue_insn[31]}}, issue_insn[31], issue_insn[7], issue_insn[30:25], issue_insn[11:8], 1'b0};
    assign imm_j = {{11{issue_insn[31]}}, issue_insn[31], issue_insn[19:12], issue_insn[20], issue_insn[30:21], 1'b0};
    assign alu_rhs = issue_insn[6:0] == OP_IMM ? imm_i : iq_rs2_value;

    always @* begin
        alu_value = 0;
        actual_next = issue_pc + 32'd4;
        taken_target = 0;
        taken = 0;
        case (issue_insn[6:0])
            OP_REG, OP_IMM: begin
                case (issue_insn[14:12])
                    3'd0: alu_value = issue_insn[6:0] == OP_REG && issue_insn[30] ? iq_rs1_value - alu_rhs : iq_rs1_value + alu_rhs;
                    3'd1: alu_value = iq_rs1_value << alu_rhs[4:0];
                    3'd2: alu_value = {31'd0, $signed(iq_rs1_value) < $signed(alu_rhs)};
                    3'd3: alu_value = {31'd0, iq_rs1_value < alu_rhs};
                    3'd4: alu_value = iq_rs1_value ^ alu_rhs;
                    3'd5: begin
                        if (issue_insn[30]) alu_value = $signed(iq_rs1_value) >>> alu_rhs[4:0];
                        else alu_value = iq_rs1_value >> alu_rhs[4:0];
                    end
                    3'd6: alu_value = iq_rs1_value | alu_rhs;
                    3'd7: alu_value = iq_rs1_value & alu_rhs;
                endcase
            end
            OP_LUI: alu_value = {issue_insn[31:12], 12'd0};
            OP_AUIPC: alu_value = issue_pc + {issue_insn[31:12], 12'd0};
            OP_BRANCH: begin
                case (issue_insn[14:12])
                    3'd0: taken = iq_rs1_value == iq_rs2_value;
                    3'd1: taken = iq_rs1_value != iq_rs2_value;
                    3'd4: taken = $signed(iq_rs1_value) < $signed(iq_rs2_value);
                    3'd5: taken = $signed(iq_rs1_value) >= $signed(iq_rs2_value);
                    3'd6: taken = iq_rs1_value < iq_rs2_value;
                    3'd7: taken = iq_rs1_value >= iq_rs2_value;
                    default: taken = 0;
                endcase
                taken_target = issue_pc + imm_b;
                if (taken) actual_next = taken_target;
            end
            OP_JAL, OP_JALR: begin
                taken = 1;
                alu_value = issue_pc + 32'd4;
                taken_target = issue_insn[6:0] == OP_JAL ? issue_pc + imm_j : (iq_rs1_value + imm_i) & 32'hfffffffe;
                actual_next = taken_target;
            end
            default: ;
        endcase
    end

endmodule
