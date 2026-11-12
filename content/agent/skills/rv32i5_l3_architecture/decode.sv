module decode (
    input logic [31:0] buf_insn,
    output logic d_load, d_store, d_control, d_ecall, d_writes,
    output logic [4:0] dec_rs1, dec_rs2, dec_rd,
    output logic [31:0] memory_imm
);
    localparam [6:0] OP_REG=7'h33, OP_IMM=7'h13, OP_LUI=7'h37, OP_AUIPC=7'h17,
        OP_LOAD=7'h03, OP_STORE=7'h23, OP_BRANCH=7'h63, OP_JAL=7'h6f, OP_JALR=7'h67;
    logic use1, use2;

    assign d_load = buf_insn[6:0] == OP_LOAD;
    assign d_store = buf_insn[6:0] == OP_STORE;
    assign d_control = buf_insn[6:0] == OP_BRANCH || buf_insn[6:0] == OP_JAL || buf_insn[6:0] == OP_JALR;
    assign d_ecall = buf_insn == 32'h00000073;
    assign dec_rd = buf_insn[11:7];
    assign d_writes = dec_rd != 0 && (d_load || buf_insn[6:0] == OP_REG || buf_insn[6:0] == OP_IMM
                      || buf_insn[6:0] == OP_LUI || buf_insn[6:0] == OP_AUIPC
                      || buf_insn[6:0] == OP_JAL || buf_insn[6:0] == OP_JALR);
    assign use1 = d_load || d_store || buf_insn[6:0] == OP_BRANCH || buf_insn[6:0] == OP_JALR
                  || buf_insn[6:0] == OP_REG || buf_insn[6:0] == OP_IMM;
    assign use2 = d_store || buf_insn[6:0] == OP_BRANCH || buf_insn[6:0] == OP_REG;
    assign dec_rs1 = use1 ? buf_insn[19:15] : 5'd0;
    assign dec_rs2 = use2 ? buf_insn[24:20] : 5'd0;
    assign memory_imm = d_store ? {{20{buf_insn[31]}}, buf_insn[31:25], buf_insn[11:7]}
                                : {{20{buf_insn[31]}}, buf_insn[31:20]};
endmodule
