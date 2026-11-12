module hazard_unit (
    input  logic clk,
    input  logic rst_n,
    input  logic id_ex_valid,
    input  logic id_ex_mem_read,
    input  logic [4:0] id_ex_rd,
    input  logic if_id_valid,
    input  logic [4:0] if_id_rs1,
    input  logic [4:0] if_id_rs2,
    input  logic ex_redirect_valid,
    input  logic mem_busy,
    input  logic id_ecall_detected,
    input  logic halted,
    output logic if_hold,
    output logic if_flush,
    output logic id_ex_hold,
    output logic id_ex_flush,
    output logic ex_mem_hold,
    output logic mem_hold,
    output logic halt_pending
);

    logic load_use;

    assign load_use = id_ex_valid & id_ex_mem_read & (id_ex_rd != 5'd0) & if_id_valid &
                      ((if_id_rs1 == id_ex_rd) | (if_id_rs2 == id_ex_rd));

    assign if_hold     = halted | mem_busy | load_use | halt_pending;
    assign if_flush    = ex_redirect_valid | halt_pending;
    assign id_ex_hold  = halted | mem_busy;
    assign id_ex_flush = ex_redirect_valid | load_use;
    assign ex_mem_hold = halted | mem_busy;
    assign mem_hold    = halted;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            halt_pending <= 1'b0;
        end else if (halted) begin
            halt_pending <= 1'b0;
        end else if (id_ecall_detected & ~ex_redirect_valid) begin
            halt_pending <= 1'b1;
        end
    end

endmodule
