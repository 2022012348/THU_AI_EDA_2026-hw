module ex_stage (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        id_ex_valid,
    input  logic [31:0] id_ex_pc,
    input  logic [31:0] id_ex_insn,
    input  logic [31:0] id_ex_rs1_data,
    input  logic [31:0] id_ex_rs2_data,
    input  logic [4:0]  id_ex_rs1,
    input  logic [4:0]  id_ex_rs2,
    input  logic [4:0]  id_ex_rd,
    input  logic [31:0] id_ex_imm,
    input  logic        id_ex_reg_write,
    input  logic        id_ex_mem_read,
    input  logic        id_ex_mem_write,
    input  logic        id_ex_branch,
    input  logic        id_ex_jal,
    input  logic        id_ex_jalr,
    input  logic        id_ex_lui,
    input  logic        id_ex_auipc,
    input  logic        id_ex_ecall,
    input  logic [3:0]  id_ex_alu_op,
    input  logic [2:0]  id_ex_funct3,
    input  logic [1:0]  fwd_rs1_sel,
    input  logic [1:0]  fwd_rs2_sel,
    input  logic [31:0] ex_mem_fwd_data,
    input  logic [31:0] mem_wb_fwd_data,
    input  logic        ex_mem_hold,
    output logic        ex_mem_valid,
    output logic [31:0] ex_mem_pc,
    output logic [31:0] ex_mem_insn,
    output logic [31:0] ex_mem_alu_result,
    output logic [31:0] ex_mem_store_data,
    output logic [4:0]  ex_mem_rd,
    output logic        ex_mem_reg_write,
    output logic        ex_mem_mem_read,
    output logic        ex_mem_mem_write,
    output logic [2:0]  ex_mem_funct3,
    output logic        ex_mem_ecall,
    output logic        redirect_valid,
    output logic [31:0] redirect_pc,
    output logic        ex_is_load,
    output logic [4:0]  ex_load_rd,
    output logic        ex_use_rs1,
    output logic        ex_use_rs2,
    output logic [4:0]  ex_rs1,
    output logic [4:0]  ex_rs2
);

    logic [31:0] rs1_fwd;
    logic [31:0] rs2_fwd;
    logic [31:0] alu_a;
    logic [31:0] alu_b;
    logic [31:0] alu_result;
    logic [31:0] link_val;
    logic [31:0] result;
    logic [31:0] store_data;
    logic [4:0]  shamt;

    logic        branch_taken;
    logic        is_ctrl;
    logic [31:0] ctrl_target;

    assign rs1_fwd = (fwd_rs1_sel == 2'd1) ? ex_mem_fwd_data :
                     (fwd_rs1_sel == 2'd2) ? mem_wb_fwd_data :
                     id_ex_rs1_data;
    assign rs2_fwd = (fwd_rs2_sel == 2'd1) ? ex_mem_fwd_data :
                     (fwd_rs2_sel == 2'd2) ? mem_wb_fwd_data :
                     id_ex_rs2_data;

    assign store_data = rs2_fwd;

    assign shamt = id_ex_insn[6:0] == 7'b0110011 ? rs2_fwd[4:0] : id_ex_imm[4:0];

    assign alu_a = id_ex_auipc ? id_ex_pc : rs1_fwd;
    assign alu_b = (id_ex_insn[6:0] == 7'b0110011) ? rs2_fwd :
                   (id_ex_insn[6:0] == 7'b0010011) ? id_ex_imm :
                   id_ex_imm;

    always_comb begin
        case (id_ex_alu_op)
            4'd0: alu_result = alu_a + alu_b;
            4'd1: alu_result = alu_a - alu_b;
            4'd2: alu_result = alu_a << shamt;
            4'd3: alu_result = ($signed(alu_a) < $signed(alu_b)) ? 32'd1 : 32'd0;
            4'd4: alu_result = (alu_a < alu_b) ? 32'd1 : 32'd0;
            4'd5: alu_result = alu_a ^ alu_b;
            4'd6: alu_result = alu_a >> shamt;
            4'd7: alu_result = $signed(alu_a) >>> shamt;
            4'd8: alu_result = alu_a | alu_b;
            4'd9: alu_result = alu_a & alu_b;
            default: alu_result = 32'b0;
        endcase
    end

    assign link_val = id_ex_pc + 32'd4;

    always_comb begin
        if (id_ex_lui)        result = id_ex_imm;
        else if (id_ex_jal || id_ex_jalr) result = link_val;
        else                  result = alu_result;
    end

    // Branch/jump resolution
    always_comb begin
        branch_taken = 1'b0;
        if (id_ex_branch) begin
            case (id_ex_funct3)
                3'b000: branch_taken = (rs1_fwd == rs2_fwd);
                3'b001: branch_taken = (rs1_fwd != rs2_fwd);
                3'b100: branch_taken = ($signed(rs1_fwd) < $signed(rs2_fwd));
                3'b101: branch_taken = ($signed(rs1_fwd) >= $signed(rs2_fwd));
                3'b110: branch_taken = (rs1_fwd < rs2_fwd);
                3'b111: branch_taken = (rs1_fwd >= rs2_fwd);
                default: branch_taken = 1'b0;
            endcase
        end
    end

    assign is_ctrl = id_ex_branch || id_ex_jal || id_ex_jalr;

    always_comb begin
        if (id_ex_jal) begin
            ctrl_target = id_ex_pc + id_ex_imm;
        end else if (id_ex_jalr) begin
            ctrl_target = (rs1_fwd + id_ex_imm) & 32'hFFFF_FFFE;
        end else begin
            ctrl_target = id_ex_pc + id_ex_imm;
        end
    end

    assign redirect_valid = id_ex_valid && is_ctrl && (id_ex_jal || id_ex_jalr || branch_taken);
    assign redirect_pc    = ctrl_target;

    assign ex_is_load = id_ex_valid && id_ex_mem_read;
    assign ex_load_rd = id_ex_rd;
    assign ex_use_rs1 = id_ex_reg_write && (id_ex_rs1 != 5'd0) &&
                        (id_ex_insn[6:0] != 7'b0110111) &&
                        (id_ex_insn[6:0] != 7'b0010111) &&
                        (id_ex_insn[6:0] != 7'b1101111);
    assign ex_use_rs2 = id_ex_reg_write && (id_ex_rs2 != 5'd0) &&
                        (id_ex_insn[6:0] == 7'b0110011);
    assign ex_rs1     = id_ex_rs1;
    assign ex_rs2     = id_ex_rs2;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            ex_mem_valid      <= 1'b0;
            ex_mem_pc         <= 32'b0;
            ex_mem_insn       <= 32'b0;
            ex_mem_alu_result <= 32'b0;
            ex_mem_store_data <= 32'b0;
            ex_mem_rd         <= 5'b0;
            ex_mem_reg_write  <= 1'b0;
            ex_mem_mem_read   <= 1'b0;
            ex_mem_mem_write  <= 1'b0;
            ex_mem_funct3     <= 3'b0;
            ex_mem_ecall      <= 1'b0;
        end else if (!ex_mem_hold) begin
            ex_mem_valid      <= id_ex_valid;
            ex_mem_pc         <= id_ex_pc;
            ex_mem_insn       <= id_ex_insn;
            ex_mem_alu_result <= result;
            ex_mem_store_data <= store_data;
            ex_mem_rd         <= id_ex_rd;
            ex_mem_reg_write  <= id_ex_reg_write;
            ex_mem_mem_read   <= id_ex_mem_read;
            ex_mem_mem_write  <= id_ex_mem_write;
            ex_mem_funct3     <= id_ex_funct3;
            ex_mem_ecall      <= id_ex_ecall;
        end
    end

endmodule
