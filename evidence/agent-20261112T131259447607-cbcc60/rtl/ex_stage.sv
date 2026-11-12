module ex_stage (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        ex_mem_hold,
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
    input  logic        id_ex_alu_src_imm,
    input  logic [3:0]  id_ex_alu_op,
    input  logic [2:0]  id_ex_funct3,
    input  logic        id_ex_ecall,
    input  logic [31:0] id_ex_pred_next_pc,
    input  logic        id_ex_pred_valid,
    input  logic [1:0]  id_ex_epoch,
    input  logic [1:0]  fwd_rs1_sel,
    input  logic [1:0]  fwd_rs2_sel,
    input  logic [31:0] ex_mem_fwd_data,
    input  logic [31:0] mem_wb_fwd_data,
    output logic        ex_redirect_valid,
    output logic [31:0] ex_redirect_pc,
    output logic [1:0]  ex_redirect_epoch,
    output logic        ex_resolve_valid,
    output logic        ex_resolve_mispredict,
    output logic [31:0] ex_resolve_pc,
    output logic [31:0] ex_resolve_actual_next_pc,
    output logic        ex_resolve_is_branch,
    output logic        ex_resolve_is_jal,
    output logic        ex_resolve_is_jalr,
    output logic        ex_resolve_taken,
    output logic [31:0] ex_resolve_target,
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
    output logic [31:0] ex_mem_pred_next_pc,
    output logic        ex_mem_pred_valid,
    output logic [1:0]  ex_mem_epoch
);

    logic [31:0] rs1_fwd, rs2_fwd;
    logic [31:0] alu_a, alu_b;
    logic [31:0] alu_result;
    logic [31:0] actual_next_pc;
    logic        is_control;
    logic        taken;
    logic [31:0] target;
    logic        mispredict;

    assign rs1_fwd = (fwd_rs1_sel == 2'b01) ? ex_mem_fwd_data :
                     (fwd_rs1_sel == 2'b10) ? mem_wb_fwd_data : id_ex_rs1_data;
    assign rs2_fwd = (fwd_rs2_sel == 2'b01) ? ex_mem_fwd_data :
                     (fwd_rs2_sel == 2'b10) ? mem_wb_fwd_data : id_ex_rs2_data;

    assign alu_a = rs1_fwd;
    assign alu_b = id_ex_alu_src_imm ? id_ex_imm : rs2_fwd;

    // ALU
    always_comb begin
        case (id_ex_alu_op)
            4'b0000: alu_result = alu_a + alu_b;
            4'b0001: alu_result = alu_a - alu_b;
            4'b0010: alu_result = alu_a << alu_b[4:0];
            4'b0011: alu_result = ($signed(alu_a) < $signed(alu_b)) ? 32'd1 : 32'd0;
            4'b0100: alu_result = (alu_a < alu_b) ? 32'd1 : 32'd0;
            4'b0101: alu_result = alu_a ^ alu_b;
            4'b0110: alu_result = alu_a >> alu_b[4:0];
            4'b0111: alu_result = $signed(alu_a) >>> alu_b[4:0];
            4'b1000: alu_result = alu_a | alu_b;
            4'b1001: alu_result = alu_a & alu_b;
            4'b1010: alu_result = id_ex_imm;
            4'b1011: alu_result = id_ex_pc + id_ex_imm;
            default: alu_result = 32'b0;
        endcase
    end

    // branch resolution
    always_comb begin
        taken = 1'b0;
        case (id_ex_funct3)
            3'b000: taken = (rs1_fwd == rs2_fwd);
            3'b001: taken = (rs1_fwd != rs2_fwd);
            3'b100: taken = ($signed(rs1_fwd) < $signed(rs2_fwd));
            3'b101: taken = ($signed(rs1_fwd) >= $signed(rs2_fwd));
            3'b110: taken = (rs1_fwd < rs2_fwd);
            3'b111: taken = (rs1_fwd >= rs2_fwd);
            default: taken = 1'b0;
        endcase
    end

    assign is_control = id_ex_branch | id_ex_jal | id_ex_jalr;

    always_comb begin
        if (id_ex_jal)
            target = id_ex_pc + id_ex_imm;
        else if (id_ex_jalr)
            target = (rs1_fwd + id_ex_imm) & 32'hFFFF_FFFE;
        else
            target = id_ex_pc + id_ex_imm;
    end

    always_comb begin
        if (id_ex_jal || id_ex_jalr)
            actual_next_pc = target;
        else if (id_ex_branch && taken)
            actual_next_pc = target;
        else
            actual_next_pc = id_ex_pc + 32'd4;
    end

    // When no prediction is carried (BTB miss / BHT not-taken), the front-end
    // fetched PC+4. A taken branch then differs from that fall-through and must
    // redirect. Compare against the implicit PC+4 prediction in that case.
    assign mispredict = id_ex_pred_valid ? (id_ex_pred_next_pc != actual_next_pc)
                                         : (actual_next_pc != (id_ex_pc + 32'd4));

    assign ex_resolve_valid          = id_ex_valid & is_control & ~ex_mem_hold;
    assign ex_resolve_mispredict     = ex_resolve_valid & mispredict;
    assign ex_resolve_pc             = id_ex_pc;
    assign ex_resolve_actual_next_pc = actual_next_pc;
    assign ex_resolve_is_branch      = id_ex_branch;
    assign ex_resolve_is_jal         = id_ex_jal;
    assign ex_resolve_is_jalr        = id_ex_jalr;
    assign ex_resolve_taken          = (id_ex_jal | id_ex_jalr) ? 1'b1 : (id_ex_branch & taken);
    assign ex_resolve_target         = target;

    assign ex_redirect_valid = ex_resolve_valid & mispredict;
    assign ex_redirect_pc    = actual_next_pc;
    assign ex_redirect_epoch = id_ex_epoch + 2'd1;

    // link value for jumps
    logic [31:0] wb_value;
    always_comb begin
        if (id_ex_jal || id_ex_jalr)
            wb_value = id_ex_pc + 32'd4;
        else
            wb_value = alu_result;
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            ex_mem_valid        <= 1'b0;
            ex_mem_pc           <= 32'b0;
            ex_mem_insn         <= 32'b0;
            ex_mem_alu_result   <= 32'b0;
            ex_mem_store_data   <= 32'b0;
            ex_mem_rd           <= 5'b0;
            ex_mem_reg_write    <= 1'b0;
            ex_mem_mem_read     <= 1'b0;
            ex_mem_mem_write    <= 1'b0;
            ex_mem_funct3       <= 3'b0;
            ex_mem_ecall        <= 1'b0;
            ex_mem_pred_next_pc <= 32'b0;
            ex_mem_pred_valid   <= 1'b0;
            ex_mem_epoch        <= 2'b0;
        end else if (!ex_mem_hold) begin
            // The instruction currently in EX is the resolving control transfer
            // when a redirect fires; it must still advance to MEM/WB and retire.
            // Younger instructions are squashed by the hazard unit via
            // if_flush / id_ex_flush, not by killing this stage's output.
            ex_mem_valid        <= id_ex_valid;
            ex_mem_pc           <= id_ex_pc;
            ex_mem_insn         <= id_ex_insn;
            ex_mem_alu_result   <= wb_value;
            ex_mem_store_data   <= rs2_fwd;
            ex_mem_rd           <= id_ex_rd;
            ex_mem_reg_write    <= id_ex_reg_write;
            ex_mem_mem_read     <= id_ex_mem_read;
            ex_mem_mem_write    <= id_ex_mem_write;
            ex_mem_funct3       <= id_ex_funct3;
            ex_mem_ecall        <= id_ex_ecall;
            ex_mem_pred_next_pc <= id_ex_pred_next_pc;
            ex_mem_pred_valid   <= id_ex_pred_valid;
            ex_mem_epoch        <= id_ex_epoch;
        end
    end

endmodule
