module rv32i5 (
    input  logic        clk,
    input  logic        rst_n,
    output logic [31:0] imem_addr,
    input  logic [31:0] imem_rdata,
    output logic        imem_valid,
    input  logic        imem_ready,
    output logic        dmem_valid,
    output logic        dmem_we,
    output logic [31:0] dmem_addr,
    output logic [31:0] dmem_wdata,
    output logic [3:0]  dmem_be,
    input  logic [31:0] dmem_rdata,
    input  logic        dmem_ready,
    input  logic [4:0]  dbg_reg_addr,
    output logic [31:0] dbg_reg_data,
    output logic        halted,
    output logic [31:0] exit_code,
    output logic [2:0]  dbg_features,
    output logic        dbg_retire_valid,
    output logic [31:0] dbg_retire_pc,
    output logic [31:0] dbg_retire_insn,
    output logic        dbg_bp_resolve,
    output logic        dbg_bp_mispredict,
    output logic        dbg_ooo_issue,
    output logic        dbg_ooo_bypass
);

    // IF <-> ID
    logic        if_id_valid;
    logic [31:0] if_id_pc;
    logic [31:0] if_id_insn;
    logic        if_id_ready;
    logic        if_id_hold;
    logic        if_id_flush;

    // ID <-> EX
    logic        id_ex_valid;
    logic [31:0] id_ex_pc;
    logic [31:0] id_ex_insn;
    logic [31:0] id_ex_rs1_data;
    logic [31:0] id_ex_rs2_data;
    logic [4:0]  id_ex_rs1;
    logic [4:0]  id_ex_rs2;
    logic [4:0]  id_ex_rd;
    logic [31:0] id_ex_imm;
    logic        id_ex_reg_write;
    logic        id_ex_mem_read;
    logic        id_ex_mem_write;
    logic        id_ex_branch;
    logic        id_ex_jal;
    logic        id_ex_jalr;
    logic        id_ex_lui;
    logic        id_ex_auipc;
    logic        id_ex_ecall;
    logic [3:0]  id_ex_alu_op;
    logic [2:0]  id_ex_funct3;
    logic        id_ex_use_rs1;
    logic        id_ex_use_rs2;
    logic        id_ex_hold;
    logic        id_ex_flush;

    // EX <-> MEM
    logic        ex_mem_valid;
    logic [31:0] ex_mem_pc;
    logic [31:0] ex_mem_insn;
    logic [31:0] ex_mem_alu_result;
    logic [31:0] ex_mem_store_data;
    logic [4:0]  ex_mem_rd;
    logic        ex_mem_reg_write;
    logic        ex_mem_mem_read;
    logic        ex_mem_mem_write;
    logic [2:0]  ex_mem_funct3;
    logic        ex_mem_ecall;
    logic        ex_mem_hold;

    // MEM <-> WB
    logic        mem_wb_valid;
    logic [31:0] mem_wb_pc;
    logic [31:0] mem_wb_insn;
    logic [31:0] mem_wb_data;
    logic [4:0]  mem_wb_rd;
    logic        mem_wb_reg_write;
    logic        mem_wb_ecall;
    logic        mem_wb_is_store;
    logic        mem_wb_store_done;
    logic        mem_hold;

    // WB
    logic        wb_reg_write;
    logic [4:0]  wb_rd;
    logic [31:0] wb_data;

    // EX forwarding / redirect
    logic [1:0]  fwd_rs1_sel;
    logic [1:0]  fwd_rs2_sel;
    logic [31:0] ex_mem_fwd_data;
    logic [31:0] mem_wb_fwd_data;
    logic        redirect_valid;
    logic [31:0] redirect_pc;
    logic        ex_is_load;
    logic [4:0]  ex_load_rd;
    logic        ex_use_rs1;
    logic        ex_use_rs2;
    logic [4:0]  ex_rs1;
    logic [4:0]  ex_rs2;

    // Hazard
    logic        mem_busy;
    logic        halt_pending;
    logic [1:0]  flush_epoch;
    logic        ecall_pending_set;
    logic [31:0] ecall_pc;

    // IF
    logic [31:0] if_pc;
    logic        cpu_req_valid;
    logic [31:0] cpu_req_addr;
    logic        cpu_req_ready;
    logic        cpu_resp_valid;
    logic [31:0] cpu_resp_data;
    logic [1:0]  if_epoch;
    logic [1:0]  if_flush_epoch;
    logic        if_flush;

    // D-cache CPU side
    logic        dcache_req_valid;
    logic        dcache_req_we;
    logic [31:0] dcache_req_addr;
    logic [31:0] dcache_req_wdata;
    logic [3:0]  dcache_req_be;
    logic        dcache_req_ready;
    logic        dcache_resp_valid;
    logic [31:0] dcache_resp_rdata;
    logic [1:0]  mem_flush_epoch;
    logic        mem_flush;

    // I-cache external
    logic        ic_mem_req_valid;
    logic        ic_mem_req_ready;
    logic [31:0] ic_mem_req_addr;
    logic        ic_mem_resp_valid;
    logic [31:0] ic_mem_resp_data;

    // D-cache external
    logic        dc_mem_req_valid;
    logic        dc_mem_req_we;
    logic [31:0] dc_mem_req_addr;
    logic [31:0] dc_mem_req_wdata;
    logic [3:0]  dc_mem_req_be;
    logic        dc_mem_req_ready;
    logic        dc_mem_resp_valid;
    logic [31:0] dc_mem_resp_data;

    // WB ecall commit
    logic        wb_ecall_commit;
    logic [31:0] arch_x10;

    // Halt state
    logic        halted_r;
    logic [31:0] exit_code_r;
    logic [31:0] imem_addr_hold;

    // External bus outputs (single driver each)
    assign imem_valid = ic_mem_req_valid & rst_n & ~halted_r;
    assign imem_addr  = halted_r ? imem_addr_hold : ic_mem_req_addr;

    assign dmem_valid = dc_mem_req_valid & rst_n & ~halted_r;
    assign dmem_we    = dc_mem_req_we;
    assign dmem_addr  = dc_mem_req_addr;
    assign dmem_wdata = dc_mem_req_wdata;
    assign dmem_be    = dc_mem_req_be;

    assign dbg_features     = 3'b001;
    assign dbg_bp_resolve   = 1'b0;
    assign dbg_bp_mispredict= 1'b0;
    assign dbg_ooo_issue    = 1'b0;
    assign dbg_ooo_bypass   = 1'b0;

    assign halted    = halted_r;
    assign exit_code = exit_code_r;

    // Forwarding data sources
    assign ex_mem_fwd_data = ex_mem_alu_result;
    assign mem_wb_fwd_data = wb_data;

    // Halt / exit_code register
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            halted_r       <= 1'b0;
            exit_code_r    <= 32'b0;
            imem_addr_hold <= 32'b0;
        end else begin
            if (!halted_r) begin
                imem_addr_hold <= ic_mem_req_addr;
            end
            if (wb_ecall_commit && !halted_r) begin
                halted_r    <= 1'b1;
                exit_code_r <= arch_x10;
            end
        end
    end

    if_stage u_if (
        .clk            (clk),
        .rst_n          (rst_n),
        .if_id_hold     (if_id_hold),
        .if_id_flush    (if_id_flush),
        .redirect_valid (redirect_valid),
        .redirect_pc    (redirect_pc),
        .redirect_epoch (flush_epoch),
        .halt_pending   (halt_pending),
        .if_pc          (if_pc),
        .if_id_valid    (if_id_valid),
        .if_id_pc       (if_id_pc),
        .if_id_insn     (if_id_insn),
        .cpu_req_valid  (cpu_req_valid),
        .cpu_req_addr   (cpu_req_addr),
        .cpu_req_ready  (cpu_req_ready),
        .cpu_resp_valid (cpu_resp_valid),
        .cpu_resp_data  (cpu_resp_data),
        .if_epoch       (if_epoch),
        .if_flush_epoch (if_flush_epoch),
        .if_flush       (if_flush)
    );

    id_stage u_id (
        .clk              (clk),
        .rst_n            (rst_n),
        .if_id_valid      (if_id_valid),
        .if_id_pc         (if_id_pc),
        .if_id_insn       (if_id_insn),
        .if_id_ready      (if_id_ready),
        .id_ex_hold       (id_ex_hold),
        .id_ex_flush      (id_ex_flush),
        .id_ex_valid      (id_ex_valid),
        .id_ex_pc         (id_ex_pc),
        .id_ex_insn       (id_ex_insn),
        .id_ex_rs1_data   (id_ex_rs1_data),
        .id_ex_rs2_data   (id_ex_rs2_data),
        .id_ex_rs1        (id_ex_rs1),
        .id_ex_rs2        (id_ex_rs2),
        .id_ex_rd         (id_ex_rd),
        .id_ex_imm        (id_ex_imm),
        .id_ex_reg_write  (id_ex_reg_write),
        .id_ex_mem_read   (id_ex_mem_read),
        .id_ex_mem_write  (id_ex_mem_write),
        .id_ex_branch     (id_ex_branch),
        .id_ex_jal        (id_ex_jal),
        .id_ex_jalr       (id_ex_jalr),
        .id_ex_lui        (id_ex_lui),
        .id_ex_auipc      (id_ex_auipc),
        .id_ex_ecall      (id_ex_ecall),
        .id_ex_alu_op     (id_ex_alu_op),
        .id_ex_funct3     (id_ex_funct3),
        .id_ex_use_rs1    (id_ex_use_rs1),
        .id_ex_use_rs2    (id_ex_use_rs2),
        .wb_reg_write     (wb_reg_write),
        .wb_rd            (wb_rd),
        .wb_data          (wb_data),
        .dbg_reg_addr     (dbg_reg_addr),
        .dbg_reg_data     (dbg_reg_data),
        .ecall_pending_set(ecall_pending_set),
        .ecall_pc         (ecall_pc)
    );

    ex_stage u_ex (
        .clk             (clk),
        .rst_n           (rst_n),
        .id_ex_valid     (id_ex_valid),
        .id_ex_pc        (id_ex_pc),
        .id_ex_insn      (id_ex_insn),
        .id_ex_rs1_data  (id_ex_rs1_data),
        .id_ex_rs2_data  (id_ex_rs2_data),
        .id_ex_rs1       (id_ex_rs1),
        .id_ex_rs2       (id_ex_rs2),
        .id_ex_rd        (id_ex_rd),
        .id_ex_imm       (id_ex_imm),
        .id_ex_reg_write (id_ex_reg_write),
        .id_ex_mem_read  (id_ex_mem_read),
        .id_ex_mem_write (id_ex_mem_write),
        .id_ex_branch    (id_ex_branch),
        .id_ex_jal       (id_ex_jal),
        .id_ex_jalr      (id_ex_jalr),
        .id_ex_lui       (id_ex_lui),
        .id_ex_auipc     (id_ex_auipc),
        .id_ex_ecall     (id_ex_ecall),
        .id_ex_alu_op    (id_ex_alu_op),
        .id_ex_funct3    (id_ex_funct3),
        .fwd_rs1_sel     (fwd_rs1_sel),
        .fwd_rs2_sel     (fwd_rs2_sel),
        .ex_mem_fwd_data (ex_mem_fwd_data),
        .mem_wb_fwd_data (mem_wb_fwd_data),
        .ex_mem_hold     (ex_mem_hold),
        .ex_mem_valid    (ex_mem_valid),
        .ex_mem_pc       (ex_mem_pc),
        .ex_mem_insn     (ex_mem_insn),
        .ex_mem_alu_result(ex_mem_alu_result),
        .ex_mem_store_data(ex_mem_store_data),
        .ex_mem_rd       (ex_mem_rd),
        .ex_mem_reg_write(ex_mem_reg_write),
        .ex_mem_mem_read (ex_mem_mem_read),
        .ex_mem_mem_write(ex_mem_mem_write),
        .ex_mem_funct3   (ex_mem_funct3),
        .ex_mem_ecall    (ex_mem_ecall),
        .redirect_valid  (redirect_valid),
        .redirect_pc     (redirect_pc),
        .ex_is_load      (ex_is_load),
        .ex_load_rd      (ex_load_rd),
        .ex_use_rs1      (ex_use_rs1),
        .ex_use_rs2      (ex_use_rs2),
        .ex_rs1          (ex_rs1),
        .ex_rs2          (ex_rs2)
    );

    mem_stage u_mem (
        .clk              (clk),
        .rst_n            (rst_n),
        .ex_mem_valid     (ex_mem_valid),
        .ex_mem_pc        (ex_mem_pc),
        .ex_mem_insn      (ex_mem_insn),
        .ex_mem_alu_result(ex_mem_alu_result),
        .ex_mem_store_data(ex_mem_store_data),
        .ex_mem_rd        (ex_mem_rd),
        .ex_mem_reg_write (ex_mem_reg_write),
        .ex_mem_mem_read  (ex_mem_mem_read),
        .ex_mem_mem_write (ex_mem_mem_write),
        .ex_mem_funct3    (ex_mem_funct3),
        .ex_mem_ecall     (ex_mem_ecall),
        .mem_hold         (mem_hold),
        .mem_wb_fwd_data  (mem_wb_fwd_data),
        .dcache_resp_rdata(dcache_resp_rdata),
        .dcache_resp_valid(dcache_resp_valid),
        .dcache_req_valid (dcache_req_valid),
        .dcache_req_we    (dcache_req_we),
        .dcache_req_addr  (dcache_req_addr),
        .dcache_req_wdata (dcache_req_wdata),
        .dcache_req_be    (dcache_req_be),
        .dcache_req_ready (dcache_req_ready),
        .mem_busy         (mem_busy),
        .mem_wb_valid     (mem_wb_valid),
        .mem_wb_pc        (mem_wb_pc),
        .mem_wb_insn      (mem_wb_insn),
        .mem_wb_data      (mem_wb_data),
        .mem_wb_rd        (mem_wb_rd),
        .mem_wb_reg_write (mem_wb_reg_write),
        .mem_wb_ecall     (mem_wb_ecall),
        .mem_wb_is_store  (mem_wb_is_store),
        .mem_wb_store_done(mem_wb_store_done),
        .mem_flush_epoch  (mem_flush_epoch),
        .mem_flush        (mem_flush)
    );

    wb_stage u_wb (
        .clk              (clk),
        .rst_n            (rst_n),
        .mem_wb_valid     (mem_wb_valid),
        .mem_wb_pc        (mem_wb_pc),
        .mem_wb_insn      (mem_wb_insn),
        .mem_wb_data      (mem_wb_data),
        .mem_wb_rd        (mem_wb_rd),
        .mem_wb_reg_write (mem_wb_reg_write),
        .mem_wb_ecall     (mem_wb_ecall),
        .mem_wb_is_store  (mem_wb_is_store),
        .mem_wb_store_done(mem_wb_store_done),
        .wb_reg_write     (wb_reg_write),
        .wb_rd            (wb_rd),
        .wb_data          (wb_data),
        .dbg_retire_valid (dbg_retire_valid),
        .dbg_retire_pc    (dbg_retire_pc),
        .dbg_retire_insn  (dbg_retire_insn),
        .halted           (),
        .exit_code        (),
        .arch_x10         (arch_x10),
        .wb_ecall_commit  (wb_ecall_commit)
    );

    hazard_unit u_hazard (
        .clk              (clk),
        .rst_n            (rst_n),
        .ex_is_load       (ex_is_load),
        .ex_load_rd       (ex_load_rd),
        .ex_use_rs1       (ex_use_rs1),
        .ex_use_rs2       (ex_use_rs2),
        .ex_rs1           (ex_rs1),
        .ex_rs2           (ex_rs2),
        .id_ex_valid      (id_ex_valid),
        .redirect_valid   (redirect_valid),
        .mem_busy         (mem_busy),
        .if_id_ready      (if_id_ready),
        .ecall_pending_set(ecall_pending_set),
        .if_id_hold       (if_id_hold),
        .id_ex_hold       (id_ex_hold),
        .ex_mem_hold      (ex_mem_hold),
        .mem_hold         (mem_hold),
        .if_id_flush      (if_id_flush),
        .id_ex_flush      (id_ex_flush),
        .halt_pending     (halt_pending),
        .flush_epoch      (flush_epoch)
    );

    forwarding_unit u_fwd (
        .ex_mem_valid    (ex_mem_valid),
        .ex_mem_reg_write(ex_mem_reg_write),
        .ex_mem_rd       (ex_mem_rd),
        .mem_wb_valid    (mem_wb_valid),
        .mem_wb_reg_write(mem_wb_reg_write),
        .mem_wb_rd       (mem_wb_rd),
        .ex_rs1          (ex_rs1),
        .ex_rs2          (ex_rs2),
        .fwd_rs1_sel     (fwd_rs1_sel),
        .fwd_rs2_sel     (fwd_rs2_sel)
    );

    icache u_icache (
        .clk            (clk),
        .rst_n          (rst_n),
        .cpu_req_valid  (cpu_req_valid),
        .cpu_req_addr   (cpu_req_addr),
        .cpu_req_ready  (cpu_req_ready),
        .cpu_resp_valid (cpu_resp_valid),
        .cpu_resp_data  (cpu_resp_data),
        .flush          (if_flush),
        .flush_epoch    (if_flush_epoch),
        .mem_req_valid  (ic_mem_req_valid),
        .mem_req_ready  (ic_mem_req_ready),
        .mem_req_addr   (ic_mem_req_addr),
        .mem_resp_valid (ic_mem_resp_valid),
        .mem_resp_data  (ic_mem_resp_data)
    );

    dcache u_dcache (
        .clk            (clk),
        .rst_n          (rst_n),
        .cpu_req_valid  (dcache_req_valid),
        .cpu_req_we     (dcache_req_we),
        .cpu_req_addr   (dcache_req_addr),
        .cpu_req_wdata  (dcache_req_wdata),
        .cpu_req_be     (dcache_req_be),
        .cpu_req_ready  (dcache_req_ready),
        .cpu_resp_valid (dcache_resp_valid),
        .cpu_resp_rdata (dcache_resp_rdata),
        .flush          (mem_flush),
        .flush_epoch    (mem_flush_epoch),
        .mem_req_valid  (dc_mem_req_valid),
        .mem_req_we     (dc_mem_req_we),
        .mem_req_addr   (dc_mem_req_addr),
        .mem_req_wdata  (dc_mem_req_wdata),
        .mem_req_be     (dc_mem_req_be),
        .mem_req_ready  (dc_mem_req_ready),
        .mem_resp_valid (dc_mem_resp_valid),
        .mem_resp_data  (dc_mem_resp_data)
    );

    assign ic_mem_req_ready = imem_ready;
    assign ic_mem_resp_valid = imem_valid & imem_ready;
    assign ic_mem_resp_data  = imem_rdata;

    assign dc_mem_req_ready = dmem_ready;
    assign dc_mem_resp_valid = dmem_valid & dmem_ready;
    assign dc_mem_resp_data  = dmem_rdata;

endmodule
