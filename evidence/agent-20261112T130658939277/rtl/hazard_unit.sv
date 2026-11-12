module hazard_unit (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        ex_is_load,
    input  logic [4:0]  ex_load_rd,
    input  logic        ex_use_rs1,
    input  logic        ex_use_rs2,
    input  logic [4:0]  ex_rs1,
    input  logic [4:0]  ex_rs2,
    input  logic        id_ex_valid,
    input  logic        redirect_valid,
    input  logic        mem_busy,
    input  logic        if_id_ready,
    input  logic        ecall_pending_set,
    output logic        if_id_hold,
    output logic        id_ex_hold,
    output logic        ex_mem_hold,
    output logic        mem_hold,
    output logic        if_id_flush,
    output logic        id_ex_flush,
    output logic        halt_pending,
    output logic [1:0]  flush_epoch
);

    logic load_use;
    logic halt_pending_r;
    logic [1:0] epoch_r;

    assign load_use = ex_is_load && (ex_load_rd != 5'd0) &&
                      ((ex_use_rs1 && (ex_rs1 == ex_load_rd)) ||
                       (ex_use_rs2 && (ex_rs2 == ex_load_rd)));

    assign halt_pending = halt_pending_r;

    assign if_id_hold  = load_use || mem_busy || halt_pending_r;
    assign id_ex_hold  = mem_busy || halt_pending_r;
    assign ex_mem_hold = mem_busy;
    assign mem_hold    = 1'b0;

    assign if_id_flush = redirect_valid;
    assign id_ex_flush = redirect_valid || (load_use && !id_ex_hold);

    assign flush_epoch = epoch_r;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            halt_pending_r <= 1'b0;
            epoch_r        <= 2'b0;
        end else begin
            if (ecall_pending_set && !redirect_valid) begin
                halt_pending_r <= 1'b1;
                epoch_r        <= epoch_r + 2'd1;
            end else if (redirect_valid) begin
                epoch_r        <= epoch_r + 2'd1;
            end
        end
    end

endmodule
