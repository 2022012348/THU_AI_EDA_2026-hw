module forwarding_unit (
    input  logic        ex_mem_valid,
    input  logic        ex_mem_reg_write,
    input  logic        ex_mem_mem_read,
    input  logic [4:0]  ex_mem_rd,
    input  logic        mem_wb_valid,
    input  logic        mem_wb_reg_write,
    input  logic [4:0]  mem_wb_rd,
    input  logic [4:0]  id_ex_rs1,
    input  logic [4:0]  id_ex_rs2,
    output logic [1:0]  fwd_rs1_sel,
    output logic [1:0]  fwd_rs2_sel
);

    logic ex_mem_ok;
    logic mem_wb_ok;

    assign ex_mem_ok = ex_mem_valid & ex_mem_reg_write & ~ex_mem_mem_read & (ex_mem_rd != 5'd0);
    assign mem_wb_ok = mem_wb_valid & mem_wb_reg_write & (mem_wb_rd != 5'd0);

    always_comb begin
        if (ex_mem_ok && ex_mem_rd == id_ex_rs1)
            fwd_rs1_sel = 2'b01;
        else if (mem_wb_ok && mem_wb_rd == id_ex_rs1)
            fwd_rs1_sel = 2'b10;
        else
            fwd_rs1_sel = 2'b00;
    end

    always_comb begin
        if (ex_mem_ok && ex_mem_rd == id_ex_rs2)
            fwd_rs2_sel = 2'b01;
        else if (mem_wb_ok && mem_wb_rd == id_ex_rs2)
            fwd_rs2_sel = 2'b10;
        else
            fwd_rs2_sel = 2'b00;
    end

endmodule
