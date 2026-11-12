module wb_stage (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        mem_wb_valid,
    input  logic [31:0] mem_wb_pc,
    input  logic [31:0] mem_wb_insn,
    input  logic [31:0] mem_wb_alu_result,
    input  logic [31:0] mem_wb_load_data,
    input  logic [4:0]  mem_wb_rd,
    input  logic        mem_wb_reg_write,
    input  logic        mem_wb_mem_read,
    input  logic        mem_wb_ecall,
    input  logic [1:0]  mem_wb_epoch,
    input  logic [31:0] arch_x10,
    output logic        wb_reg_write,
    output logic [4:0]  wb_rd,
    output logic [31:0] wb_data,
    output logic        wb_retire_valid,
    output logic [31:0] wb_retire_pc,
    output logic [31:0] wb_retire_insn,
    output logic        wb_ecall_commit,
    output logic        halted,
    output logic [31:0] exit_code
);

    logic [31:0] wb_data_int;
    logic [31:0] x10_shadow;

    assign wb_data_int = mem_wb_mem_read ? mem_wb_load_data : mem_wb_alu_result;

    assign wb_reg_write    = mem_wb_valid & mem_wb_reg_write & (mem_wb_rd != 5'd0) & ~halted;
    assign wb_rd           = mem_wb_rd;
    assign wb_data         = wb_data_int;
    assign wb_retire_valid = mem_wb_valid & ~halted;
    assign wb_retire_pc    = mem_wb_pc;
    assign wb_retire_insn  = mem_wb_insn;
    assign wb_ecall_commit = mem_wb_valid & mem_wb_ecall & ~halted;

    // Track the architectural x10 from the writeback stream so the ECALL
    // commit captures the true final x10, independent of dbg_reg_addr.
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            x10_shadow <= 32'b0;
        end else if (wb_reg_write && (wb_rd == 5'd10)) begin
            x10_shadow <= wb_data;
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            halted    <= 1'b0;
            exit_code <= 32'b0;
        end else if (wb_ecall_commit) begin
            halted    <= 1'b1;
            exit_code <= x10_shadow;
        end
    end

endmodule
