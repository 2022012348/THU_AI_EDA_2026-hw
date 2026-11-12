module decode (
    input  logic [31:0] buf_insn,
    output logic        d_load,
    output logic        d_store,
    output logic        d_control,
    output logic        d_ecall,
    output logic        d_writes,
    output logic [4:0]  dec_rs1,
    output logic [4:0]  dec_rs2,
    output logic [4:0]  dec_rd,
    output logic [31:0] memory_imm
);
    logic [6:0] opcode;
    logic [2:0] funct3;

    assign opcode = buf_insn[6:0];
    assign funct3 = buf_insn[14:12];
    assign dec_rs1 = buf_insn[19:15];
    assign dec_rs2 = buf_insn[24:20];
    assign dec_rd  = buf_insn[11:7];

    assign d_load    = (opcode == 7'b0000011);
    assign d_store   = (opcode == 7'b0100011);
    assign d_control = (opcode == 7'b1100011) || (opcode == 7'b1101111) || (opcode == 7'b1100111);
    assign d_ecall   = (buf_insn == 32'h0000_0073);

    always_comb begin
        d_writes = 1'b0;
        if (opcode == 7'b0110011) d_writes = 1'b1;
        else if (opcode == 7'b0010011) d_writes = 1'b1;
        else if (opcode == 7'b0000011) d_writes = 1'b1;
        else if (opcode == 7'b0110111) d_writes = 1'b1;
        else if (opcode == 7'b0010111) d_writes = 1'b1;
        else if (opcode == 7'b1101111) d_writes = 1'b1;
        else if (opcode == 7'b1100111) d_writes = 1'b1;
    end

    always_comb begin
        memory_imm = 32'b0;
        if (opcode == 7'b0000011) begin
            memory_imm = {{20{buf_insn[31]}}, buf_insn[31:20]};
        end else if (opcode == 7'b0100011) begin
            memory_imm = {{20{buf_insn[31]}}, buf_insn[31:25], buf_insn[11:7]};
        end
    end
endmodule
