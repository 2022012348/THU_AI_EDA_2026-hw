module id_stage (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        id_ex_hold,
    input  logic        id_ex_flush,
    input  logic        if_id_valid,
    input  logic [31:0] if_id_pc,
    input  logic [31:0] if_id_insn,
    input  logic [31:0] if_id_pred_next_pc,
    input  logic        if_id_pred_valid,
    input  logic [1:0]  if_id_epoch,
    input  logic        wb_reg_write,
    input  logic [4:0]  wb_rd,
    input  logic [31:0] wb_data,
    input  logic [4:0]  dbg_reg_addr,
    output logic [31:0] dbg_reg_data,
    output logic        id_ecall_detected,
    output logic        id_ex_valid,
    output logic [31:0] id_ex_pc,
    output logic [31:0] id_ex_insn,
    output logic [31:0] id_ex_rs1_data,
    output logic [31:0] id_ex_rs2_data,
    output logic [4:0]  id_ex_rs1,
    output logic [4:0]  id_ex_rs2,
    output logic [4:0]  id_ex_rd,
    output logic [31:0] id_ex_imm,
    output logic        id_ex_reg_write,
    output logic        id_ex_mem_read,
    output logic        id_ex_mem_write,
    output logic        id_ex_branch,
    output logic        id_ex_jal,
    output logic        id_ex_jalr,
    output logic        id_ex_alu_src_imm,
    output logic [3:0]  id_ex_alu_op,
    output logic [2:0]  id_ex_funct3,
    output logic        id_ex_ecall,
    output logic [31:0] id_ex_pred_next_pc,
    output logic        id_ex_pred_valid,
    output logic [1:0]  id_ex_epoch
);

    logic [31:0] regs [0:31];

    // decode
    logic [6:0]  opcode;
    logic [4:0]  rs1, rs2, rd;
    logic [2:0]  funct3;
    logic [6:0]  funct7;

    logic [31:0] rs1_data, rs2_data;
    logic [31:0] imm;
    logic        reg_write, mem_read, mem_write, branch, jal, jalr, alu_src_imm;
    logic [3:0]  alu_op;
    logic        ecall;

    assign opcode = if_id_insn[6:0];
    assign rd     = if_id_insn[11:7];
    assign funct3 = if_id_insn[14:12];
    assign rs1    = if_id_insn[19:15];
    assign rs2    = if_id_insn[24:20];
    assign funct7 = if_id_insn[31:25];

    // register read with WB bypass
    assign rs1_data = (rs1 == 5'd0) ? 32'b0 :
                      (wb_reg_write && wb_rd == rs1 && wb_rd != 5'd0) ? wb_data : regs[rs1];
    assign rs2_data = (rs2 == 5'd0) ? 32'b0 :
                      (wb_reg_write && wb_rd == rs2 && wb_rd != 5'd0) ? wb_data : regs[rs2];

    assign dbg_reg_data = (dbg_reg_addr == 5'd0) ? 32'b0 : regs[dbg_reg_addr];

    // immediate
    always_comb begin
        case (opcode)
            7'b0010011, 7'b0000011, 7'b1100111: imm = {{20{if_id_insn[31]}}, if_id_insn[31:20]};
            7'b0100011: imm = {{20{if_id_insn[31]}}, if_id_insn[31:25], if_id_insn[11:7]};
            7'b1100011: imm = {{20{if_id_insn[31]}}, if_id_insn[7], if_id_insn[30:25], if_id_insn[11:8], 1'b0};
            7'b0110111, 7'b0010111: imm = {if_id_insn[31:12], 12'b0};
            7'b1101111: imm = {{12{if_id_insn[31]}}, if_id_insn[19:12], if_id_insn[20], if_id_insn[30:21], 1'b0};
            default: imm = 32'b0;
        endcase
    end

    // control decode
    always_comb begin
        reg_write   = 1'b0;
        mem_read    = 1'b0;
        mem_write   = 1'b0;
        branch      = 1'b0;
        jal         = 1'b0;
        jalr        = 1'b0;
        alu_src_imm = 1'b0;
        alu_op      = 4'b0000;
        ecall       = 1'b0;
        case (opcode)
            7'b0110011: begin // R
                reg_write = 1'b1;
                case (funct3)
                    3'b000: alu_op = funct7[5] ? 4'b0001 : 4'b0000; // SUB/ADD
                    3'b001: alu_op = 4'b0010; // SLL
                    3'b010: alu_op = 4'b0011; // SLT
                    3'b011: alu_op = 4'b0100; // SLTU
                    3'b100: alu_op = 4'b0101; // XOR
                    3'b101: alu_op = funct7[5] ? 4'b0111 : 4'b0110; // SRA/SRL
                    3'b110: alu_op = 4'b1000; // OR
                    3'b111: alu_op = 4'b1001; // AND
                    default: alu_op = 4'b0000;
                endcase
            end
            7'b0010011: begin // I ALU
                reg_write   = 1'b1;
                alu_src_imm = 1'b1;
                case (funct3)
                    3'b000: alu_op = 4'b0000; // ADDI
                    3'b010: alu_op = 4'b0011; // SLTI
                    3'b011: alu_op = 4'b0100; // SLTIU
                    3'b100: alu_op = 4'b0101; // XORI
                    3'b110: alu_op = 4'b1000; // ORI
                    3'b111: alu_op = 4'b1001; // ANDI
                    3'b001: alu_op = 4'b0010; // SLLI
                    3'b101: alu_op = funct7[5] ? 4'b0111 : 4'b0110; // SRAI/SRLI
                    default: alu_op = 4'b0000;
                endcase
            end
            7'b0000011: begin // load
                reg_write   = 1'b1;
                mem_read    = 1'b1;
                alu_src_imm = 1'b1;
                alu_op      = 4'b0000;
            end
            7'b0100011: begin // store
                mem_write   = 1'b1;
                alu_src_imm = 1'b1;
                alu_op      = 4'b0000;
            end
            7'b1100011: begin // branch
                branch      = 1'b1;
                alu_op      = 4'b0000;
            end
            7'b1101111: begin // JAL
                reg_write = 1'b1;
                jal       = 1'b1;
            end
            7'b1100111: begin // JALR
                reg_write   = 1'b1;
                jalr        = 1'b1;
                alu_src_imm = 1'b1;
                alu_op      = 4'b0000;
            end
            7'b0110111: begin // LUI
                reg_write = 1'b1;
                alu_op    = 4'b1010;
            end
            7'b0010111: begin // AUIPC
                reg_write = 1'b1;
                alu_op    = 4'b1011;
            end
            7'b1110011: begin
                if (if_id_insn == 32'h0000_0073)
                    ecall = 1'b1;
            end
            default: ;
        endcase
    end

    assign id_ecall_detected = if_id_valid & ecall;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            for (int i = 0; i < 32; i++) regs[i] <= 32'b0;
        end else if (wb_reg_write && wb_rd != 5'd0) begin
            regs[wb_rd] <= wb_data;
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            id_ex_valid        <= 1'b0;
            id_ex_pc           <= 32'b0;
            id_ex_insn         <= 32'b0;
            id_ex_rs1_data     <= 32'b0;
            id_ex_rs2_data     <= 32'b0;
            id_ex_rs1          <= 5'b0;
            id_ex_rs2          <= 5'b0;
            id_ex_rd           <= 5'b0;
            id_ex_imm          <= 32'b0;
            id_ex_reg_write    <= 1'b0;
            id_ex_mem_read     <= 1'b0;
            id_ex_mem_write    <= 1'b0;
            id_ex_branch       <= 1'b0;
            id_ex_jal          <= 1'b0;
            id_ex_jalr         <= 1'b0;
            id_ex_alu_src_imm  <= 1'b0;
            id_ex_alu_op       <= 4'b0;
            id_ex_funct3       <= 3'b0;
            id_ex_ecall        <= 1'b0;
            id_ex_pred_next_pc <= 32'b0;
            id_ex_pred_valid   <= 1'b0;
            id_ex_epoch        <= 2'b0;
        end else if (id_ex_flush) begin
            id_ex_valid <= 1'b0;
        end else if (!id_ex_hold) begin
            id_ex_valid        <= if_id_valid;
            id_ex_pc           <= if_id_pc;
            id_ex_insn         <= if_id_insn;
            id_ex_rs1_data     <= rs1_data;
            id_ex_rs2_data     <= rs2_data;
            id_ex_rs1          <= rs1;
            id_ex_rs2          <= rs2;
            id_ex_rd           <= rd;
            id_ex_imm          <= imm;
            id_ex_reg_write    <= reg_write;
            id_ex_mem_read     <= mem_read;
            id_ex_mem_write    <= mem_write;
            id_ex_branch       <= branch;
            id_ex_jal          <= jal;
            id_ex_jalr         <= jalr;
            id_ex_alu_src_imm  <= alu_src_imm;
            id_ex_alu_op       <= alu_op;
            id_ex_funct3       <= funct3;
            id_ex_ecall        <= ecall;
            id_ex_pred_next_pc <= if_id_pred_next_pc;
            id_ex_pred_valid   <= if_id_pred_valid;
            id_ex_epoch        <= if_id_epoch;
        end
    end

endmodule
