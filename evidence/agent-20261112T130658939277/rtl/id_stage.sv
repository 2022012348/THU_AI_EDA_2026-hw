module id_stage (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        if_id_valid,
    input  logic [31:0] if_id_pc,
    input  logic [31:0] if_id_insn,
    output logic        if_id_ready,
    input  logic        id_ex_hold,
    input  logic        id_ex_flush,
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
    output logic        id_ex_lui,
    output logic        id_ex_auipc,
    output logic        id_ex_ecall,
    output logic [3:0]  id_ex_alu_op,
    output logic [2:0]  id_ex_funct3,
    output logic        id_ex_use_rs1,
    output logic        id_ex_use_rs2,
    input  logic        wb_reg_write,
    input  logic [4:0]  wb_rd,
    input  logic [31:0] wb_data,
    input  logic [4:0]  dbg_reg_addr,
    output logic [31:0] dbg_reg_data,
    output logic        ecall_pending_set,
    output logic [31:0] ecall_pc
);

    logic [31:0] regs [0:31];

    logic [6:0]  opcode;
    logic [4:0]  rs1;
    logic [4:0]  rs2;
    logic [4:0]  rd;
    logic [2:0]  funct3;
    logic [6:0]  funct7;

    logic [31:0] imm_i;
    logic [31:0] imm_s;
    logic [31:0] imm_b;
    logic [31:0] imm_u;
    logic [31:0] imm_j;
    logic [31:0] imm_out;

    logic [31:0] rs1_val;
    logic [31:0] rs2_val;

    logic        c_reg_write;
    logic        c_mem_read;
    logic        c_mem_write;
    logic        c_branch;
    logic        c_jal;
    logic        c_jalr;
    logic        c_lui;
    logic        c_auipc;
    logic        c_ecall;
    logic [3:0]  c_alu_op;
    logic        c_use_rs1;
    logic        c_use_rs2;

    logic        is_ecall;
    logic        accept;

    assign opcode = if_id_insn[6:0];
    assign rd     = if_id_insn[11:7];
    assign funct3 = if_id_insn[14:12];
    assign rs1    = if_id_insn[19:15];
    assign rs2    = if_id_insn[24:20];
    assign funct7 = if_id_insn[31:25];

    assign imm_i = {{20{if_id_insn[31]}}, if_id_insn[31:20]};
    assign imm_s = {{20{if_id_insn[31]}}, if_id_insn[31:25], if_id_insn[11:7]};
    assign imm_b = {{19{if_id_insn[31]}}, if_id_insn[31], if_id_insn[7],
                    if_id_insn[30:25], if_id_insn[11:8], 1'b0};
    assign imm_u = {if_id_insn[31:12], 12'b0};
    assign imm_j = {{11{if_id_insn[31]}}, if_id_insn[31], if_id_insn[19:12],
                    if_id_insn[20], if_id_insn[30:21], 1'b0};

    assign is_ecall = (if_id_insn == 32'h0000_0073);

    // Register file read with WB bypass
    assign rs1_val = (rs1 == 5'd0) ? 32'b0 :
                     ((wb_reg_write && (wb_rd == rs1)) ? wb_data : regs[rs1]);
    assign rs2_val = (rs2 == 5'd0) ? 32'b0 :
                     ((wb_reg_write && (wb_rd == rs2)) ? wb_data : regs[rs2]);

    assign dbg_reg_data = (dbg_reg_addr == 5'd0) ? 32'b0 : regs[dbg_reg_addr];

    // Decode
    always_comb begin
        c_reg_write = 1'b0;
        c_mem_read  = 1'b0;
        c_mem_write = 1'b0;
        c_branch    = 1'b0;
        c_jal       = 1'b0;
        c_jalr      = 1'b0;
        c_lui       = 1'b0;
        c_auipc     = 1'b0;
        c_ecall     = 1'b0;
        c_alu_op    = 4'd0;
        c_use_rs1   = 1'b0;
        c_use_rs2   = 1'b0;
        imm_out     = 32'b0;

        case (opcode)
            7'b0110011: begin // R-type
                c_reg_write = 1'b1;
                c_use_rs1   = 1'b1;
                c_use_rs2   = 1'b1;
                case (funct3)
                    3'b000: c_alu_op = funct7[5] ? 4'd1 : 4'd0; // SUB/ADD
                    3'b001: c_alu_op = 4'd2; // SLL
                    3'b010: c_alu_op = 4'd3; // SLT
                    3'b011: c_alu_op = 4'd4; // SLTU
                    3'b100: c_alu_op = 4'd5; // XOR
                    3'b101: c_alu_op = funct7[5] ? 4'd7 : 4'd6; // SRA/SRL
                    3'b110: c_alu_op = 4'd8; // OR
                    3'b111: c_alu_op = 4'd9; // AND
                    default: c_alu_op = 4'd0;
                endcase
            end
            7'b0010011: begin // I-type ALU
                c_reg_write = 1'b1;
                c_use_rs1   = 1'b1;
                imm_out     = imm_i;
                case (funct3)
                    3'b000: c_alu_op = 4'd0; // ADDI
                    3'b010: c_alu_op = 4'd3; // SLTI
                    3'b011: c_alu_op = 4'd4; // SLTIU
                    3'b100: c_alu_op = 4'd5; // XORI
                    3'b110: c_alu_op = 4'd8; // ORI
                    3'b111: c_alu_op = 4'd9; // ANDI
                    3'b001: c_alu_op = 4'd2; // SLLI
                    3'b101: c_alu_op = funct7[5] ? 4'd7 : 4'd6; // SRAI/SRLI
                    default: c_alu_op = 4'd0;
                endcase
            end
            7'b0000011: begin // Load
                c_reg_write = 1'b1;
                c_mem_read  = 1'b1;
                c_use_rs1   = 1'b1;
                c_alu_op    = 4'd0;
                imm_out     = imm_i;
            end
            7'b0100011: begin // Store
                c_mem_write = 1'b1;
                c_use_rs1   = 1'b1;
                c_use_rs2   = 1'b1;
                c_alu_op    = 4'd0;
                imm_out     = imm_s;
            end
            7'b1100011: begin // Branch
                c_branch    = 1'b1;
                c_use_rs1   = 1'b1;
                c_use_rs2   = 1'b1;
                imm_out     = imm_b;
            end
            7'b1101111: begin // JAL
                c_reg_write = 1'b1;
                c_jal       = 1'b1;
                imm_out     = imm_j;
            end
            7'b1100111: begin // JALR
                c_reg_write = 1'b1;
                c_jalr      = 1'b1;
                c_use_rs1   = 1'b1;
                imm_out     = imm_i;
            end
            7'b0110111: begin // LUI
                c_reg_write = 1'b1;
                c_lui       = 1'b1;
                imm_out     = imm_u;
            end
            7'b0010111: begin // AUIPC
                c_reg_write = 1'b1;
                c_auipc     = 1'b1;
                imm_out     = imm_u;
            end
            7'b1110011: begin // ECALL
                if (is_ecall) c_ecall = 1'b1;
            end
            default: ;
        endcase
    end

    assign if_id_ready = !id_ex_hold;
    assign accept      = if_id_valid && if_id_ready;
    assign ecall_pending_set = accept && c_ecall;
    assign ecall_pc          = if_id_pc;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            id_ex_valid     <= 1'b0;
            id_ex_pc        <= 32'b0;
            id_ex_insn      <= 32'b0;
            id_ex_rs1_data  <= 32'b0;
            id_ex_rs2_data  <= 32'b0;
            id_ex_rs1       <= 5'b0;
            id_ex_rs2       <= 5'b0;
            id_ex_rd        <= 5'b0;
            id_ex_imm       <= 32'b0;
            id_ex_reg_write <= 1'b0;
            id_ex_mem_read  <= 1'b0;
            id_ex_mem_write <= 1'b0;
            id_ex_branch    <= 1'b0;
            id_ex_jal       <= 1'b0;
            id_ex_jalr      <= 1'b0;
            id_ex_lui       <= 1'b0;
            id_ex_auipc     <= 1'b0;
            id_ex_ecall     <= 1'b0;
            id_ex_alu_op    <= 4'b0;
            id_ex_funct3    <= 3'b0;
            id_ex_use_rs1   <= 1'b0;
            id_ex_use_rs2   <= 1'b0;
        end else if (id_ex_flush) begin
            id_ex_valid     <= 1'b0;
        end else if (!id_ex_hold) begin
            if (accept) begin
                id_ex_valid     <= 1'b1;
                id_ex_pc        <= if_id_pc;
                id_ex_insn      <= if_id_insn;
                id_ex_rs1_data  <= rs1_val;
                id_ex_rs2_data  <= rs2_val;
                id_ex_rs1       <= rs1;
                id_ex_rs2       <= rs2;
                id_ex_rd        <= rd;
                id_ex_imm       <= imm_out;
                id_ex_reg_write <= c_reg_write;
                id_ex_mem_read  <= c_mem_read;
                id_ex_mem_write <= c_mem_write;
                id_ex_branch    <= c_branch;
                id_ex_jal       <= c_jal;
                id_ex_jalr      <= c_jalr;
                id_ex_lui       <= c_lui;
                id_ex_auipc     <= c_auipc;
                id_ex_ecall     <= c_ecall;
                id_ex_alu_op    <= c_alu_op;
                id_ex_funct3    <= funct3;
                id_ex_use_rs1   <= c_use_rs1;
                id_ex_use_rs2   <= c_use_rs2;
            end else begin
                id_ex_valid     <= 1'b0;
            end
        end
    end

    // Register file write
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            for (int i = 0; i < 32; i++) begin
                regs[i] <= 32'b0;
            end
        end else if (wb_reg_write && (wb_rd != 5'd0)) begin
            regs[wb_rd] <= wb_data;
        end
    end

endmodule
