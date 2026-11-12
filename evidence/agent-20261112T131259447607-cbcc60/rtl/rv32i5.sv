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
    logic [31:0] if_id_pred_next_pc;
    logic        if_id_pred_valid;
    logic [1:0]  if_id_epoch;

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
    logic        id_ex_alu_src_imm;
    logic [3:0]  id_ex_alu_op;
    logic [2:0]  id_ex_funct3;
    logic        id_ex_ecall;
    logic [31:0] id_ex_pred_next_pc;
    logic        id_ex_pred_valid;
    logic [1:0]  id_ex_epoch;

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
    logic [31:0] ex_mem_pred_next_pc;
    logic        ex_mem_pred_valid;
    logic [1:0]  ex_mem_epoch;

    // MEM <-> WB
    logic        mem_wb_valid;
    logic [31:0] mem_wb_pc;
    logic [31:0] mem_wb_insn;
    logic [31:0] mem_wb_alu_result;
    logic [31:0] mem_wb_load_data;
    logic [4:0]  mem_wb_rd;
    logic        mem_wb_reg_write;
    logic        mem_wb_mem_read;
    logic        mem_wb_ecall;
    logic [1:0]  mem_wb_epoch;

    // WB -> ID
    logic        wb_reg_write;
    logic [4:0]  wb_rd;
    logic [31:0] wb_data;

    // EX redirect
    logic        ex_redirect_valid;
    logic [31:0] ex_redirect_pc;
    logic [1:0]  ex_redirect_epoch;

    // EX resolve
    logic        ex_resolve_valid;
    logic        ex_resolve_mispredict;
    logic [31:0] ex_resolve_pc;
    logic [31:0] ex_resolve_actual_next_pc;
    logic        ex_resolve_is_branch;
    logic        ex_resolve_is_jal;
    logic        ex_resolve_is_jalr;
    logic        ex_resolve_taken;
    logic [31:0] ex_resolve_target;

    // Forwarding
    logic [1:0]  fwd_rs1_sel;
    logic [1:0]  fwd_rs2_sel;
    logic [31:0] ex_mem_fwd_data;
    logic [31:0] mem_wb_fwd_data;

    // Hazard
    logic        if_hold;
    logic        if_flush;
    logic        id_ex_hold;
    logic        id_ex_flush;
    logic        ex_mem_hold;
    logic        mem_hold;
    logic        halt_pending;
    logic        id_ecall_detected;
    logic        mem_busy;

    // IF <-> icache
    logic        icache_cpu_req_valid;
    logic [31:0] icache_cpu_req_addr;
    logic        icache_cpu_req_ready;
    logic        icache_cpu_resp_valid;
    logic [31:0] icache_cpu_resp_data;
    logic [1:0]  icache_flush_epoch;
    logic        icache_flush;
    logic        icache_mem_req_valid;
    logic        icache_mem_req_ready;
    logic [31:0] icache_mem_req_addr;
    logic        icache_mem_resp_valid;
    logic [31:0] icache_mem_resp_data;

    // MEM <-> dcache
    logic        dcache_cpu_req_valid;
    logic        dcache_cpu_req_we;
    logic [31:0] dcache_cpu_req_addr;
    logic [31:0] dcache_cpu_req_wdata;
    logic [3:0]  dcache_cpu_req_be;
    logic        dcache_cpu_req_ready;
    logic        dcache_cpu_resp_valid;
    logic [31:0] dcache_cpu_resp_rdata;
    logic [1:0]  dcache_flush_epoch;
    logic        dcache_flush;
    logic        dcache_mem_req_valid;
    logic        dcache_mem_req_ready;
    logic [31:0] dcache_mem_req_addr;
    logic        dcache_mem_req_we;
    logic [31:0] dcache_mem_req_wdata;
    logic [3:0]  dcache_mem_req_be;
    logic        dcache_mem_resp_valid;
    logic [31:0] dcache_mem_resp_data;

    // Branch predictor
    logic [31:0] bp_lookup_pc;
    logic [31:0] bp_lookup_next_pc;
    logic        bp_lookup_valid;
    logic        bp_train_valid;
    logic [31:0] bp_train_pc;
    logic [31:0] bp_train_actual_next_pc;
    logic        bp_train_is_branch;
    logic        bp_train_is_jal;
    logic        bp_train_is_jalr;
    logic        bp_train_taken;
    logic [31:0] bp_train_target;
    logic        bp_resolve;
    logic        bp_mispredict;

    // Fetch epoch / pc
    logic [1:0]  fetch_epoch;
    logic [31:0] fetch_pc;

    // WB retire
    logic        wb_retire_valid;
    logic [31:0] wb_retire_pc;
    logic [31:0] wb_retire_insn;
    logic        wb_ecall_commit;

    // arch x10
    logic [31:0] arch_x10;

    // imem hold register
    logic [31:0] imem_addr_hold;
    logic        imem_addr_hold_valid;

    // External bus outputs (single driver)
    logic        imem_valid_int;
    logic [31:0] imem_addr_int;
    logic        dmem_valid_int;
    logic        dmem_we_int;
    logic [31:0] dmem_addr_int;
    logic [31:0] dmem_wdata_int;
    logic [3:0]  dmem_be_int;

    // ---- imem external bus ----
    assign imem_valid_int = icache_mem_req_valid & rst_n & ~halted;
    assign imem_addr_int  = icache_mem_req_addr;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            imem_addr_hold       <= 32'b0;
            imem_addr_hold_valid <= 1'b0;
        end else if (halted) begin
            // hold
        end else if (imem_valid_int) begin
            imem_addr_hold       <= imem_addr_int;
            imem_addr_hold_valid <= 1'b1;
        end
    end

    assign imem_valid = imem_valid_int;
    assign imem_addr  = halted ? (imem_addr_hold_valid ? imem_addr_hold : 32'b0) : imem_addr_int;

    // ---- dmem external bus ----
    assign dmem_valid_int = dcache_mem_req_valid & rst_n & ~halted;
    assign dmem_we_int    = dcache_mem_req_we;
    assign dmem_addr_int  = dcache_mem_req_addr;
    assign dmem_wdata_int = dcache_mem_req_wdata;
    assign dmem_be_int    = dcache_mem_req_be;

    assign dmem_valid = dmem_valid_int;
    assign dmem_we    = dmem_we_int;
    assign dmem_addr  = dmem_addr_int;
    assign dmem_wdata = dmem_wdata_int;
    assign dmem_be    = dmem_be_int;

    // ---- debug ----
    assign dbg_features     = 3'b011;
    assign dbg_retire_valid = wb_retire_valid & rst_n & ~halted;
    assign dbg_retire_pc    = wb_retire_pc;
    assign dbg_retire_insn  = wb_retire_insn;
    assign dbg_bp_resolve    = bp_resolve & rst_n & ~halted;
    assign dbg_bp_mispredict = bp_mispredict & rst_n & ~halted;
    assign dbg_ooo_issue    = 1'b0;
    assign dbg_ooo_bypass   = 1'b0;

    // ---- forwarding data ----
    assign ex_mem_fwd_data = ex_mem_alu_result;
    assign mem_wb_fwd_data = wb_data;

    // ---- icache flush ----
    assign icache_flush = ex_redirect_valid;

    // ---- dcache flush ----
    assign dcache_flush = 1'b0;

    // ---- predictor training ----
    assign bp_train_valid          = ex_resolve_valid;
    assign bp_train_pc             = ex_resolve_pc;
    assign bp_train_actual_next_pc = ex_resolve_actual_next_pc;
    assign bp_train_is_branch      = ex_resolve_is_branch;
    assign bp_train_is_jal         = ex_resolve_is_jal;
    assign bp_train_is_jalr        = ex_resolve_is_jalr;
    assign bp_train_taken          = ex_resolve_taken;
    assign bp_train_target         = ex_resolve_target;

    // ---- instances ----
    if_stage u_if (
        .clk                  (clk),
        .rst_n                (rst_n),
        .if_redirect_valid    (ex_redirect_valid),
        .if_redirect_pc       (ex_redirect_pc),
        .if_redirect_epoch    (ex_redirect_epoch),
        .if_hold              (if_hold),
        .if_flush             (if_flush),
        .bp_lookup_next_pc    (bp_lookup_next_pc),
        .bp_lookup_valid      (bp_lookup_valid),
        .bp_lookup_pc         (bp_lookup_pc),
        .icache_cpu_req_valid (icache_cpu_req_valid),
        .icache_cpu_req_addr  (icache_cpu_req_addr),
        .icache_cpu_req_ready (icache_cpu_req_ready),
        .icache_cpu_resp_valid(icache_cpu_resp_valid),
        .icache_cpu_resp_data (icache_cpu_resp_data),
        .icache_flush_epoch   (icache_flush_epoch),
        .if_id_valid          (if_id_valid),
        .if_id_pc             (if_id_pc),
        .if_id_insn           (if_id_insn),
        .if_id_pred_next_pc   (if_id_pred_next_pc),
        .if_id_pred_valid     (if_id_pred_valid),
        .if_id_epoch          (if_id_epoch),
        .fetch_epoch          (fetch_epoch),
        .fetch_pc             (fetch_pc)
    );

    id_stage u_id (
        .clk               (clk),
        .rst_n             (rst_n),
        .id_ex_hold        (id_ex_hold),
        .id_ex_flush       (id_ex_flush),
        .if_id_valid       (if_id_valid),
        .if_id_pc          (if_id_pc),
        .if_id_insn        (if_id_insn),
        .if_id_pred_next_pc(if_id_pred_next_pc),
        .if_id_pred_valid  (if_id_pred_valid),
        .if_id_epoch       (if_id_epoch),
        .wb_reg_write      (wb_reg_write),
        .wb_rd             (wb_rd),
        .wb_data           (wb_data),
        .dbg_reg_addr      (dbg_reg_addr),
        .dbg_reg_data      (dbg_reg_data),
        .id_ecall_detected (id_ecall_detected),
        .id_ex_valid       (id_ex_valid),
        .id_ex_pc          (id_ex_pc),
        .id_ex_insn        (id_ex_insn),
        .id_ex_rs1_data    (id_ex_rs1_data),
        .id_ex_rs2_data    (id_ex_rs2_data),
        .id_ex_rs1         (id_ex_rs1),
        .id_ex_rs2         (id_ex_rs2),
        .id_ex_rd          (id_ex_rd),
        .id_ex_imm         (id_ex_imm),
        .id_ex_reg_write   (id_ex_reg_write),
        .id_ex_mem_read    (id_ex_mem_read),
        .id_ex_mem_write   (id_ex_mem_write),
        .id_ex_branch      (id_ex_branch),
        .id_ex_jal         (id_ex_jal),
        .id_ex_jalr        (id_ex_jalr),
        .id_ex_alu_src_imm (id_ex_alu_src_imm),
        .id_ex_alu_op      (id_ex_alu_op),
        .id_ex_funct3      (id_ex_funct3),
        .id_ex_ecall       (id_ex_ecall),
        .id_ex_pred_next_pc(id_ex_pred_next_pc),
        .id_ex_pred_valid  (id_ex_pred_valid),
        .id_ex_epoch       (id_ex_epoch)
    );

    ex_stage u_ex (
        .clk                 (clk),
        .rst_n               (rst_n),
        .ex_mem_hold         (ex_mem_hold),
        .id_ex_valid         (id_ex_valid),
        .id_ex_pc            (id_ex_pc),
        .id_ex_insn          (id_ex_insn),
        .id_ex_rs1_data      (id_ex_rs1_data),
        .id_ex_rs2_data      (id_ex_rs2_data),
        .id_ex_rs1           (id_ex_rs1),
        .id_ex_rs2           (id_ex_rs2),
        .id_ex_rd            (id_ex_rd),
        .id_ex_imm           (id_ex_imm),
        .id_ex_reg_write     (id_ex_reg_write),
        .id_ex_mem_read      (id_ex_mem_read),
        .id_ex_mem_write     (id_ex_mem_write),
        .id_ex_branch        (id_ex_branch),
        .id_ex_jal           (id_ex_jal),
        .id_ex_jalr          (id_ex_jalr),
        .id_ex_alu_src_imm   (id_ex_alu_src_imm),
        .id_ex_alu_op        (id_ex_alu_op),
        .id_ex_funct3        (id_ex_funct3),
        .id_ex_ecall         (id_ex_ecall),
        .id_ex_pred_next_pc  (id_ex_pred_next_pc),
        .id_ex_pred_valid    (id_ex_pred_valid),
        .id_ex_epoch         (id_ex_epoch),
        .fwd_rs1_sel         (fwd_rs1_sel),
        .fwd_rs2_sel         (fwd_rs2_sel),
        .ex_mem_fwd_data     (ex_mem_fwd_data),
        .mem_wb_fwd_data     (mem_wb_fwd_data),
        .ex_redirect_valid   (ex_redirect_valid),
        .ex_redirect_pc      (ex_redirect_pc),
        .ex_redirect_epoch   (ex_redirect_epoch),
        .ex_resolve_valid    (ex_resolve_valid),
        .ex_resolve_mispredict(ex_resolve_mispredict),
        .ex_resolve_pc       (ex_resolve_pc),
        .ex_resolve_actual_next_pc(ex_resolve_actual_next_pc),
        .ex_resolve_is_branch(ex_resolve_is_branch),
        .ex_resolve_is_jal   (ex_resolve_is_jal),
        .ex_resolve_is_jalr  (ex_resolve_is_jalr),
        .ex_resolve_taken    (ex_resolve_taken),
        .ex_resolve_target   (ex_resolve_target),
        .ex_mem_valid        (ex_mem_valid),
        .ex_mem_pc           (ex_mem_pc),
        .ex_mem_insn         (ex_mem_insn),
        .ex_mem_alu_result   (ex_mem_alu_result),
        .ex_mem_store_data   (ex_mem_store_data),
        .ex_mem_rd           (ex_mem_rd),
        .ex_mem_reg_write    (ex_mem_reg_write),
        .ex_mem_mem_read     (ex_mem_mem_read),
        .ex_mem_mem_write    (ex_mem_mem_write),
        .ex_mem_funct3       (ex_mem_funct3),
        .ex_mem_ecall        (ex_mem_ecall),
        .ex_mem_pred_next_pc (ex_mem_pred_next_pc),
        .ex_mem_pred_valid   (ex_mem_pred_valid),
        .ex_mem_epoch        (ex_mem_epoch)
    );

    mem_stage u_mem (
        .clk                  (clk),
        .rst_n                (rst_n),
        .mem_hold             (mem_hold),
        .ex_mem_valid         (ex_mem_valid),
        .ex_mem_pc            (ex_mem_pc),
        .ex_mem_insn          (ex_mem_insn),
        .ex_mem_alu_result    (ex_mem_alu_result),
        .ex_mem_store_data    (ex_mem_store_data),
        .ex_mem_rd            (ex_mem_rd),
        .ex_mem_reg_write     (ex_mem_reg_write),
        .ex_mem_mem_read      (ex_mem_mem_read),
        .ex_mem_mem_write     (ex_mem_mem_write),
        .ex_mem_funct3        (ex_mem_funct3),
        .ex_mem_ecall         (ex_mem_ecall),
        .ex_mem_pred_next_pc  (ex_mem_pred_next_pc),
        .ex_mem_pred_valid    (ex_mem_pred_valid),
        .ex_mem_epoch         (ex_mem_epoch),
        .mem_wb_fwd_data      (mem_wb_fwd_data),
        .dcache_cpu_req_valid (dcache_cpu_req_valid),
        .dcache_cpu_req_we    (dcache_cpu_req_we),
        .dcache_cpu_req_addr  (dcache_cpu_req_addr),
        .dcache_cpu_req_wdata (dcache_cpu_req_wdata),
        .dcache_cpu_req_be    (dcache_cpu_req_be),
        .dcache_cpu_req_ready (dcache_cpu_req_ready),
        .dcache_cpu_resp_valid(dcache_cpu_resp_valid),
        .dcache_cpu_resp_rdata(dcache_cpu_resp_rdata),
        .dcache_flush_epoch   (dcache_flush_epoch),
        .mem_busy             (mem_busy),
        .mem_wb_valid         (mem_wb_valid),
        .mem_wb_pc            (mem_wb_pc),
        .mem_wb_insn          (mem_wb_insn),
        .mem_wb_alu_result    (mem_wb_alu_result),
        .mem_wb_load_data     (mem_wb_load_data),
        .mem_wb_rd            (mem_wb_rd),
        .mem_wb_reg_write     (mem_wb_reg_write),
        .mem_wb_mem_read      (mem_wb_mem_read),
        .mem_wb_ecall         (mem_wb_ecall),
        .mem_wb_epoch         (mem_wb_epoch)
    );

    wb_stage u_wb (
        .clk             (clk),
        .rst_n           (rst_n),
        .mem_wb_valid    (mem_wb_valid),
        .mem_wb_pc       (mem_wb_pc),
        .mem_wb_insn     (mem_wb_insn),
        .mem_wb_alu_result(mem_wb_alu_result),
        .mem_wb_load_data(mem_wb_load_data),
        .mem_wb_rd       (mem_wb_rd),
        .mem_wb_reg_write(mem_wb_reg_write),
        .mem_wb_mem_read (mem_wb_mem_read),
        .mem_wb_ecall    (mem_wb_ecall),
        .mem_wb_epoch    (mem_wb_epoch),
        .arch_x10        (arch_x10),
        .wb_reg_write    (wb_reg_write),
        .wb_rd           (wb_rd),
        .wb_data         (wb_data),
        .wb_retire_valid (wb_retire_valid),
        .wb_retire_pc    (wb_retire_pc),
        .wb_retire_insn  (wb_retire_insn),
        .wb_ecall_commit (wb_ecall_commit),
        .halted          (halted),
        .exit_code       (exit_code)
    );

    hazard_unit u_hazard (
        .clk              (clk),
        .rst_n            (rst_n),
        .id_ex_valid      (id_ex_valid),
        .id_ex_mem_read   (id_ex_mem_read),
        .id_ex_rd         (id_ex_rd),
        .if_id_valid      (if_id_valid),
        .if_id_rs1        (if_id_insn[19:15]),
        .if_id_rs2        (if_id_insn[24:20]),
        .ex_redirect_valid(ex_redirect_valid),
        .mem_busy         (mem_busy),
        .id_ecall_detected(id_ecall_detected),
        .halted           (halted),
        .if_hold          (if_hold),
        .if_flush         (if_flush),
        .id_ex_hold       (id_ex_hold),
        .id_ex_flush      (id_ex_flush),
        .ex_mem_hold      (ex_mem_hold),
        .mem_hold         (mem_hold),
        .halt_pending     (halt_pending)
    );

    forwarding_unit u_fwd (
        .ex_mem_valid   (ex_mem_valid),
        .ex_mem_reg_write(ex_mem_reg_write),
        .ex_mem_mem_read(ex_mem_mem_read),
        .ex_mem_rd      (ex_mem_rd),
        .mem_wb_valid   (mem_wb_valid),
        .mem_wb_reg_write(mem_wb_reg_write),
        .mem_wb_rd      (mem_wb_rd),
        .id_ex_rs1      (id_ex_rs1),
        .id_ex_rs2      (id_ex_rs2),
        .fwd_rs1_sel    (fwd_rs1_sel),
        .fwd_rs2_sel    (fwd_rs2_sel)
    );

    icache u_icache (
        .clk            (clk),
        .rst_n          (rst_n),
        .cpu_req_valid  (icache_cpu_req_valid),
        .cpu_req_addr   (icache_cpu_req_addr),
        .cpu_req_ready  (icache_cpu_req_ready),
        .cpu_resp_valid (icache_cpu_resp_valid),
        .cpu_resp_data  (icache_cpu_resp_data),
        .flush          (icache_flush),
        .flush_epoch    (icache_flush_epoch),
        .mem_req_valid  (icache_mem_req_valid),
        .mem_req_ready  (icache_mem_req_ready),
        .mem_req_addr   (icache_mem_req_addr),
        .mem_resp_valid (icache_mem_resp_valid),
        .mem_resp_data  (icache_mem_resp_data)
    );

    dcache u_dcache (
        .clk            (clk),
        .rst_n          (rst_n),
        .cpu_req_valid  (dcache_cpu_req_valid),
        .cpu_req_we     (dcache_cpu_req_we),
        .cpu_req_addr   (dcache_cpu_req_addr),
        .cpu_req_wdata  (dcache_cpu_req_wdata),
        .cpu_req_be     (dcache_cpu_req_be),
        .cpu_req_ready  (dcache_cpu_req_ready),
        .cpu_resp_valid (dcache_cpu_resp_valid),
        .cpu_resp_rdata (dcache_cpu_resp_rdata),
        .flush          (dcache_flush),
        .flush_epoch    (dcache_flush_epoch),
        .mem_req_valid  (dcache_mem_req_valid),
        .mem_req_ready  (dcache_mem_req_ready),
        .mem_req_addr   (dcache_mem_req_addr),
        .mem_req_we     (dcache_mem_req_we),
        .mem_req_wdata  (dcache_mem_req_wdata),
        .mem_req_be     (dcache_mem_req_be),
        .mem_resp_valid (dcache_mem_resp_valid),
        .mem_resp_data  (dcache_mem_resp_data)
    );

    branch_predictor u_bp (
        .clk                    (clk),
        .rst_n                  (rst_n),
        .bp_lookup_pc           (bp_lookup_pc),
        .bp_lookup_next_pc      (bp_lookup_next_pc),
        .bp_lookup_valid        (bp_lookup_valid),
        .bp_train_valid         (bp_train_valid),
        .bp_train_pc            (bp_train_pc),
        .bp_train_actual_next_pc(bp_train_actual_next_pc),
        .bp_train_is_branch     (bp_train_is_branch),
        .bp_train_is_jal        (bp_train_is_jal),
        .bp_train_is_jalr       (bp_train_is_jalr),
        .bp_train_taken         (bp_train_taken),
        .bp_train_target        (bp_train_target),
        .bp_resolve             (bp_resolve),
        .bp_mispredict          (bp_mispredict)
    );

    // external bus ready/resp wiring
    assign icache_mem_req_ready  = imem_ready;
    assign icache_mem_resp_valid = imem_valid_int & imem_ready;
    assign icache_mem_resp_data  = imem_rdata;

    assign dcache_mem_req_ready  = dmem_ready;
    assign dcache_mem_resp_valid = dmem_valid_int & dmem_ready;
    assign dcache_mem_resp_data  = dmem_rdata;

    // arch x10 from ID register file
    assign arch_x10 = dbg_reg_data;

endmodule
