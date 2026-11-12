module forwarding_unit (
    input  logic        ex_mem_valid,
    input  logic        ex_mem_reg_write,
    input  logic [4:0]  ex_mem_rd,
    input  logic        mem_wb_valid,
    input  logic        mem_wb_reg_write,
    input  logic [4:0]  mem_wb_rd,
    input  logic [4:0]  ex_rs1,
    input  logic [4:0]  ex_rs2,
    output logic [1:0]  fwd_rs1_sel,
    output logic [1:0]  fwd_rs2_sel
);

    logic ex_mem_hit_rs1;
    logic ex_mem_hit_rs2;
    logic mem_wb_hit_rs1;
    logic mem_wb_hit_rs2;

    assign ex_mem_hit_rs1 = ex_mem_valid && ex_mem_reg_write && (ex_mem_rd != 5'd0) &&
                            (ex_mem_rd == ex_rs1);
    assign ex_mem_hit_rs2 = ex_mem_valid && ex_mem_reg_write && (ex_mem_rd != 5'd0) &&
                            (ex_mem_rd == ex_rs2);
    assign mem_wb_hit_rs1 = mem_wb_valid && mem_wb_reg_write && (mem_wb_rd != 5'd0) &&
                            (mem_wb_rd == ex_rs1);
    assign mem_wb_hit_rs2 = mem_wb_valid && mem_wb_reg_write && (mem_wb_rd != 5'd0) &&
                            (mem_wb_rd == ex_rs2);

    always_comb begin
        if (ex_mem_hit_rs1)      fwd_rs1_sel = 2'd1;
        else if (mem_wb_hit_rs1) fwd_rs1_sel = 2'd2;
        else                     fwd_rs1_sel = 2'd0;

        if (ex_mem_hit_rs2)      fwd_rs2_sel = 2'd1;
        else if (mem_wb_hit_rs2) fwd_rs2_sel = 2'd2;
        else                     fwd_rs2_sel = 2'd0;
    end

endmodule
