module wb_stage (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        mem_wb_valid,
    input  logic [31:0] mem_wb_pc,
    input  logic [31:0] mem_wb_insn,
    input  logic [31:0] mem_wb_data,
    input  logic [4:0]  mem_wb_rd,
    input  logic        mem_wb_reg_write,
    input  logic        mem_wb_ecall,
    input  logic        mem_wb_is_store,
    input  logic        mem_wb_store_done,
    output logic        wb_reg_write,
    output logic [4:0]  wb_rd,
    output logic [31:0] wb_data,
    output logic        dbg_retire_valid,
    output logic [31:0] dbg_retire_pc,
    output logic [31:0] dbg_retire_insn,
    output logic        halted,
    output logic [31:0] exit_code,
    output logic [31:0] arch_x10,
    output logic        wb_ecall_commit
);

    logic        commit;
    logic [31:0] x10_r;

    assign commit = mem_wb_valid && (!mem_wb_is_store || mem_wb_store_done);

    assign wb_reg_write     = commit && mem_wb_reg_write && (mem_wb_rd != 5'd0);
    assign wb_rd            = mem_wb_rd;
    assign wb_data          = mem_wb_data;
    assign dbg_retire_valid = commit;
    assign dbg_retire_pc    = mem_wb_pc;
    assign dbg_retire_insn  = mem_wb_insn;
    assign wb_ecall_commit  = commit && mem_wb_ecall;
    assign arch_x10         = x10_r;
    assign halted           = 1'b0;
    assign exit_code        = 32'b0;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            x10_r <= 32'b0;
        end else if (wb_reg_write && (mem_wb_rd == 5'd10)) begin
            x10_r <= mem_wb_data;
        end
    end

endmodule
