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

    // ------------------------------------------------------------------
    // Global state
    // ------------------------------------------------------------------
    logic [1:0]  cur_epoch;
    logic        halted_r;
    logic [31:0] exit_code_r;
    logic [31:0] last_imem_addr;

    // ------------------------------------------------------------------
    // Fetch front-end
    // ------------------------------------------------------------------
    logic [31:0] fetch_pc;
    logic        fetch_outstanding;
    logic [31:0] fetch_pc_saved;
    logic [31:0] fetch_pred_saved;
    logic [1:0]  fetch_epoch_saved;

    logic        buf_valid;
    logic [31:0] buf_pc;
    logic [31:0] buf_insn;
    logic [31:0] buf_pred;
    logic [1:0]  buf_epoch;

    // I-cache <-> top
    logic        ic_cpu_req_valid;
    logic [31:0] ic_cpu_req_addr;
    logic        ic_cpu_req_ready;
    logic        ic_cpu_resp_valid;
    logic [31:0] ic_cpu_resp_data;
    logic        ic_flush;
    logic [1:0]  ic_flush_epoch;
    logic        ic_mem_req_valid;
    logic        ic_mem_req_ready;
    logic [31:0] ic_mem_req_addr;
    logic        ic_mem_resp_valid;
    logic [31:0] ic_mem_resp_data;

    // Predictor
    logic        bp_hit;
    logic [31:0] bp_target;
    logic        bp_pred_taken;
    logic        bp_train_valid;
    logic [31:0] bp_train_pc;
    logic [31:0] bp_train_target;
    logic        bp_train_taken;
    logic        bp_train_is_branch;
    logic        bp_train_is_jal;
    logic        bp_train_is_jalr;

    // Decode
    logic        d_load;
    logic        d_store;
    logic        d_control;
    logic        d_ecall;
    logic        d_writes;
    logic [4:0]  dec_rs1;
    logic [4:0]  dec_rs2;
    logic [4:0]  dec_rd;
    logic [31:0] memory_imm;

    // Rename
    logic [5:0]  prs1;
    logic [5:0]  prs2;
    logic [5:0]  new_prd;
    logic [5:0]  old_prd;
    logic        can_allocate;
    logic [39:0] ready_bits;
    logic [31:0] iq_rs1_value;
    logic [31:0] iq_rs2_value;
    logic [31:0] lsu_rs1_value;
    logic [31:0] lsu_rs2_value;
    logic [5:0]  iq_rs1_idx;
    logic [5:0]  iq_rs2_idx;
    logic [5:0]  lsu_rs1_idx;
    logic [5:0]  lsu_rs2_idx;
    logic [31:0] committed_x10;

    // ROB
    logic        rob_alloc_valid;
    logic        rob_alloc_ready;
    logic [2:0]  rob_alloc_idx;
    logic [2:0]  rob_head;
    logic [31:0] rob_alloc_pc;
    logic [31:0] rob_alloc_insn;
    logic [4:0]  rob_alloc_rd;
    logic [5:0]  rob_alloc_prd;
    logic [5:0]  rob_alloc_old_prd;
    logic        rob_alloc_writes;
    logic        rob_alloc_ecall;
    logic        rob_alu_complete;
    logic        rob_mem_complete;
    logic        rob_recover;
    logic [2:0]  rob_alu_tag;
    logic [2:0]  rob_mem_tag;
    logic        rob_commit_valid;
    logic [31:0] rob_commit_pc;
    logic [31:0] rob_commit_insn;
    logic [4:0]  rob_commit_rd;
    logic [5:0]  rob_commit_prd;
    logic [5:0]  rob_commit_old_prd;
    logic        rob_commit_writes;
    logic        rob_commit_ecall;

    // Issue queue
    logic        iq_squash;
    logic        iq_enqueue;
    logic [2:0]  iq_disp_rob_idx;
    logic [31:0] iq_enq_pc;
    logic [31:0] iq_enq_insn;
    logic [31:0] iq_enq_prediction;
    logic [5:0]  iq_enq_prs1;
    logic [5:0]  iq_enq_prs2;
    logic [5:0]  iq_enq_prd;
    logic        iq_enq_control;
    logic        iq_issue_ready;
    logic        iq_issue_valid;
    logic [2:0]  iq_issue_tag;
    logic [31:0] iq_issue_pc;
    logic [31:0] iq_issue_insn;
    logic [31:0] iq_issue_prediction;
    logic [5:0]  iq_issue_prd;
    logic        iq_issue_control;

    // LSU
    logic        lsu_squash;
    logic        lsu_enqueue;
    logic [2:0]  lsu_disp_rob_idx;
    logic [5:0]  lsu_enq_prs1;
    logic [5:0]  lsu_enq_prs2;
    logic [5:0]  lsu_enq_prd;
    logic [31:0] lsu_enq_imm;
    logic [2:0]  lsu_enq_funct3;
    logic        lsu_enq_store;
    logic        lsu_enq_ready;
    logic        lsu_cpu_req_valid;
    logic        lsu_cpu_req_we;
    logic [31:0] lsu_cpu_req_addr;
    logic [31:0] lsu_cpu_req_wdata;
    logic [3:0]  lsu_cpu_req_be;
    logic        lsu_cpu_req_ready;
    logic        lsu_cpu_resp_valid;
    logic [31:0] lsu_cpu_resp_rdata;
    logic        lsu_complete_valid;
    logic        lsu_wait_load;
    logic [2:0]  lsu_complete_tag;
    logic [5:0]  lsu_complete_prd;
    logic [31:0] lsu_complete_value;

    // D-cache <-> top
    logic        dc_cpu_req_valid;
    logic        dc_cpu_req_we;
    logic [31:0] dc_cpu_req_addr;
    logic [31:0] dc_cpu_req_wdata;
    logic [3:0]  dc_cpu_req_be;
    logic        dc_cpu_req_ready;
    logic        dc_cpu_resp_valid;
    logic [31:0] dc_cpu_resp_rdata;
    logic        dc_flush;
    logic [1:0]  dc_flush_epoch;
    logic        dc_mem_req_valid;
    logic        dc_mem_req_ready;
    logic [31:0] dc_mem_req_addr;
    logic        dc_mem_req_we;
    logic [31:0] dc_mem_req_wdata;
    logic [3:0]  dc_mem_req_be;
    logic        dc_mem_resp_valid;
    logic [31:0] dc_mem_resp_data;

    // ALU
    logic [31:0] alu_value;
    logic [31:0] alu_actual_next;
    logic [31:0] alu_taken_target;
    logic        alu_taken;

    // ------------------------------------------------------------------
    // Dispatch / control
    // ------------------------------------------------------------------
    logic        squash;
    logic        redirect;
    logic [31:0] redirect_pc;
    logic        ecall_pending;
    logic        dispatch_accept;
    logic        is_mem;
    logic        is_int;
    logic        int_accept;
    logic        lsu_accept;
    logic        fetch_consume;
    logic        fetch_accept;
    logic        ic_resp_ok;
    logic        bp_lookup_valid;
    logic [31:0] bp_lookup_pc;
    logic [31:0] pred_next_pc;
    logic        control_resolve;
    logic        control_mispredict;
    logic        control_is_branch;
    logic        control_is_jal;
    logic        control_is_jalr;
    logic        int_issue_fire;
    logic        lsu_launch_fire;
    logic        bypass_event;

    // ------------------------------------------------------------------
    // Fetch control
    // ------------------------------------------------------------------
    assign ic_cpu_req_valid = !halted_r && !ecall_pending && !fetch_outstanding && !buf_valid && !squash;
    assign ic_cpu_req_addr  = fetch_pc;
    assign ic_flush         = squash;
    assign ic_flush_epoch   = cur_epoch;

    assign bp_lookup_valid = ic_cpu_req_valid;
    assign bp_lookup_pc    = fetch_pc;

    assign pred_next_pc = bp_hit ? bp_target : (fetch_pc + 32'd4);

    assign ic_resp_ok = ic_cpu_resp_valid && fetch_outstanding &&
                        (fetch_epoch_saved == cur_epoch) && !squash && !halted_r;

    assign fetch_accept = ic_cpu_req_valid && ic_cpu_req_ready;

    assign fetch_consume = buf_valid && dispatch_accept;

    // ------------------------------------------------------------------
    // Decode of buffered instruction
    // ------------------------------------------------------------------
    decode u_decode (
        .buf_insn  (buf_insn),
        .d_load    (d_load),
        .d_store   (d_store),
        .d_control (d_control),
        .d_ecall   (d_ecall),
        .d_writes  (d_writes),
        .dec_rs1   (dec_rs1),
        .dec_rs2   (dec_rs2),
        .dec_rd    (dec_rd),
        .memory_imm(memory_imm)
    );

    assign is_mem = d_load || d_store;
    assign is_int = !is_mem && !d_ecall;

    // ------------------------------------------------------------------
    // Rename
    // ------------------------------------------------------------------
    assign dispatch_accept = buf_valid && can_allocate && rob_alloc_ready &&
                             (d_ecall || (is_mem ? lsu_enq_ready : 1'b1)) &&
                             !squash && !halted_r && !ecall_pending;

    rename_unit u_rename (
        .clk            (clk),
        .rst_n          (rst_n),
        .rs1            (dec_rs1),
        .rs2            (dec_rs2),
        .rd             (dec_rd),
        .dbg_addr       (dbg_reg_addr),
        .writes_rd      (d_writes),
        .dispatch       (dispatch_accept),
        .checkpoint     (dispatch_accept && d_control),
        .dispatch_tag   (rob_alloc_idx),
        .recover_tag    (rob_head),
        .recover        (squash),
        .commit         (rob_commit_valid),
        .commit_writes  (rob_commit_writes),
        .commit_rd      (rob_commit_rd),
        .commit_prd     (rob_commit_prd),
        .commit_old_prd (rob_commit_old_prd),
        .alu_wb         (int_issue_fire),
        .mem_wb         (lsu_complete_valid),
        .alu_prd        (iq_issue_prd),
        .mem_prd        (lsu_complete_prd),
        .alu_value      (alu_value),
        .mem_value      (lsu_complete_value),
        .iq_rs1_idx     (iq_rs1_idx),
        .iq_rs2_idx     (iq_rs2_idx),
        .lsu_rs1_idx    (lsu_rs1_idx),
        .lsu_rs2_idx    (lsu_rs2_idx),
        .iq_rs1_value   (iq_rs1_value),
        .iq_rs2_value   (iq_rs2_value),
        .lsu_rs1_value  (lsu_rs1_value),
        .lsu_rs2_value  (lsu_rs2_value),
        .prs1           (prs1),
        .prs2           (prs2),
        .new_prd        (new_prd),
        .old_prd        (old_prd),
        .can_allocate   (can_allocate),
        .ready_bits     (ready_bits),
        .dbg_data       (dbg_reg_data),
        .committed_x10  (committed_x10)
    );

    // ------------------------------------------------------------------
    // ROB
    // ------------------------------------------------------------------
    assign rob_alloc_valid = dispatch_accept;
    assign rob_alloc_pc    = buf_pc;
    assign rob_alloc_insn  = buf_insn;
    assign rob_alloc_rd    = dec_rd;
    assign rob_alloc_prd   = new_prd;
    assign rob_alloc_old_prd = old_prd;
    assign rob_alloc_writes  = d_writes;
    assign rob_alloc_ecall   = d_ecall;

    assign rob_alu_complete = int_issue_fire;
    assign rob_alu_tag      = iq_issue_tag;
    assign rob_mem_complete = lsu_complete_valid;
    assign rob_mem_tag      = lsu_complete_tag;
    assign rob_recover      = squash;

    reorder_buffer u_rob (
        .clk             (clk),
        .rst_n           (rst_n),
        .alloc_valid     (rob_alloc_valid),
        .alloc_ready     (rob_alloc_ready),
        .alloc_rob_idx   (rob_alloc_idx),
        .head            (rob_head),
        .alloc_pc        (rob_alloc_pc),
        .alloc_insn      (rob_alloc_insn),
        .alloc_rd        (rob_alloc_rd),
        .alloc_prd       (rob_alloc_prd),
        .alloc_old_prd   (rob_alloc_old_prd),
        .alloc_writes    (rob_alloc_writes),
        .alloc_ecall     (rob_alloc_ecall),
        .alu_complete    (rob_alu_complete),
        .mem_complete    (rob_mem_complete),
        .recover         (rob_recover),
        .alu_tag         (rob_alu_tag),
        .mem_tag         (rob_mem_tag),
        .commit_valid    (rob_commit_valid),
        .commit_pc       (rob_commit_pc),
        .commit_insn     (rob_commit_insn),
        .commit_rd       (rob_commit_rd),
        .commit_prd      (rob_commit_prd),
        .commit_old_prd  (rob_commit_old_prd),
        .commit_writes   (rob_commit_writes),
        .commit_ecall    (rob_commit_ecall)
    );

    // ------------------------------------------------------------------
    // Issue queue
    // ------------------------------------------------------------------
    assign iq_squash        = squash;
    assign iq_enqueue       = dispatch_accept && is_int;
    assign iq_disp_rob_idx  = rob_alloc_idx;
    assign iq_enq_pc        = buf_pc;
    assign iq_enq_insn      = buf_insn;
    assign iq_enq_prediction = buf_pred;
    assign iq_enq_prs1      = prs1;
    assign iq_enq_prs2      = prs2;
    assign iq_enq_prd       = new_prd;
    assign iq_enq_control   = d_control;

    issue_queue u_iq (
        .clk            (clk),
        .rst_n          (rst_n),
        .squash         (iq_squash),
        .enqueue        (iq_enqueue),
        .disp_rob_idx   (iq_disp_rob_idx),
        .rob_head       (rob_head),
        .enq_pc         (iq_enq_pc),
        .enq_insn       (iq_enq_insn),
        .enq_prediction (iq_enq_prediction),
        .enq_prs1       (iq_enq_prs1),
        .enq_prs2       (iq_enq_prs2),
        .enq_prd        (iq_enq_prd),
        .enq_control    (iq_enq_control),
        .ready_bits     (ready_bits),
        .issue_ready    (iq_issue_ready),
        .issue_valid    (iq_issue_valid),
        .issue_tag      (iq_issue_tag),
        .issue_pc       (iq_issue_pc),
        .issue_insn     (iq_issue_insn),
        .issue_prediction(iq_issue_prediction),
        .iq_rs1_idx     (iq_rs1_idx),
        .iq_rs2_idx     (iq_rs2_idx),
        .issue_prd      (iq_issue_prd),
        .issue_control  (iq_issue_control)
    );

    // ------------------------------------------------------------------
    // ALU
    // ------------------------------------------------------------------
    alu u_alu (
        .issue_pc     (iq_issue_pc),
        .issue_insn   (iq_issue_insn),
        .iq_rs1_value (iq_rs1_value),
        .iq_rs2_value (iq_rs2_value),
        .alu_value    (alu_value),
        .actual_next  (alu_actual_next),
        .taken_target (alu_taken_target),
        .taken        (alu_taken)
    );

    // Integer issue: control instructions only when at ROB head.
    // NOTE: do NOT gate issue on squash here. The control instruction that
    // resolves at the ROB head must issue on the same edge it produces the
    // redirect, so that int_issue_fire marks it complete in the ROB. Gating
    // on squash would create a combinational loop
    // (squash -> iq_issue_ready -> int_issue_fire -> control_resolve ->
    //  control_mispredict -> squash). A younger non-control cannot issue on
    // the squash edge because squash only asserts when the head control is
    // the selected issue entry.
    assign iq_issue_ready = iq_issue_valid && !halted_r && !lsu_launch_fire &&
                            (!iq_issue_control || (iq_issue_tag == rob_head));

    assign int_issue_fire = iq_issue_valid && iq_issue_ready;

    // ------------------------------------------------------------------
    // LSU
    // ------------------------------------------------------------------
    assign lsu_squash       = squash;
    assign lsu_enqueue      = dispatch_accept && is_mem;
    assign lsu_disp_rob_idx = rob_alloc_idx;
    assign lsu_enq_prs1     = prs1;
    assign lsu_enq_prs2     = prs2;
    assign lsu_enq_prd      = new_prd;
    assign lsu_enq_imm      = memory_imm;
    assign lsu_enq_funct3   = buf_insn[14:12];
    assign lsu_enq_store    = d_store;

    load_store_unit u_lsu (
        .clk             (clk),
        .rst_n           (rst_n),
        .squash          (lsu_squash),
        .enqueue         (lsu_enqueue),
        .disp_rob_idx    (lsu_disp_rob_idx),
        .rob_head        (rob_head),
        .enq_prs1        (lsu_enq_prs1),
        .enq_prs2        (lsu_enq_prs2),
        .enq_prd         (lsu_enq_prd),
        .enq_imm         (lsu_enq_imm),
        .enq_funct3      (lsu_enq_funct3),
        .enq_store       (lsu_enq_store),
        .ready_bits      (ready_bits),
        .lsu_rs1_idx     (lsu_rs1_idx),
        .lsu_rs2_idx     (lsu_rs2_idx),
        .lsu_rs1_value   (lsu_rs1_value),
        .lsu_rs2_value   (lsu_rs2_value),
        .enq_ready       (lsu_enq_ready),
        .cpu_req_valid   (lsu_cpu_req_valid),
        .cpu_req_we      (lsu_cpu_req_we),
        .cpu_req_addr    (lsu_cpu_req_addr),
        .cpu_req_wdata   (lsu_cpu_req_wdata),
        .cpu_req_be      (lsu_cpu_req_be),
        .cpu_req_ready   (lsu_cpu_req_ready),
        .cpu_resp_valid  (lsu_cpu_resp_valid),
        .cpu_resp_rdata  (lsu_cpu_resp_rdata),
        .complete_valid  (lsu_complete_valid),
        .wait_load       (lsu_wait_load),
        .complete_tag    (lsu_complete_tag),
        .complete_prd    (lsu_complete_prd),
        .complete_value  (lsu_complete_value)
    );

    assign lsu_launch_fire = lsu_cpu_req_valid && lsu_cpu_req_ready;

    // ------------------------------------------------------------------
    // D-cache
    // ------------------------------------------------------------------
    assign dc_cpu_req_valid = lsu_cpu_req_valid;
    assign dc_cpu_req_we    = lsu_cpu_req_we;
    assign dc_cpu_req_addr  = lsu_cpu_req_addr;
    assign dc_cpu_req_wdata = lsu_cpu_req_wdata;
    assign dc_cpu_req_be    = lsu_cpu_req_be;
    assign lsu_cpu_req_ready = dc_cpu_req_ready;
    assign lsu_cpu_resp_valid = dc_cpu_resp_valid;
    assign lsu_cpu_resp_rdata = dc_cpu_resp_rdata;
    assign dc_flush         = 1'b0;
    assign dc_flush_epoch   = cur_epoch;

    dcache u_dcache (
        .clk             (clk),
        .rst_n           (rst_n),
        .cpu_req_valid   (dc_cpu_req_valid),
        .cpu_req_we      (dc_cpu_req_we),
        .cpu_req_addr    (dc_cpu_req_addr),
        .cpu_req_wdata   (dc_cpu_req_wdata),
        .cpu_req_be      (dc_cpu_req_be),
        .cpu_req_ready   (dc_cpu_req_ready),
        .cpu_resp_valid  (dc_cpu_resp_valid),
        .cpu_resp_rdata  (dc_cpu_resp_rdata),
        .flush           (dc_flush),
        .flush_epoch     (dc_flush_epoch),
        .mem_req_valid   (dc_mem_req_valid),
        .mem_req_ready   (dc_mem_req_ready),
        .mem_req_addr    (dc_mem_req_addr),
        .mem_req_we      (dc_mem_req_we),
        .mem_req_wdata   (dc_mem_req_wdata),
        .mem_req_be      (dc_mem_req_be),
        .mem_resp_valid  (dc_mem_resp_valid),
        .mem_resp_data   (dc_mem_resp_data)
    );

    // ------------------------------------------------------------------
    // I-cache
    // ------------------------------------------------------------------
    icache u_icache (
        .clk             (clk),
        .rst_n           (rst_n),
        .cpu_req_valid   (ic_cpu_req_valid),
        .cpu_req_addr    (ic_cpu_req_addr),
        .cpu_req_ready   (ic_cpu_req_ready),
        .cpu_resp_valid  (ic_cpu_resp_valid),
        .cpu_resp_data   (ic_cpu_resp_data),
        .flush           (ic_flush),
        .flush_epoch     (ic_flush_epoch),
        .mem_req_valid   (ic_mem_req_valid),
        .mem_req_ready   (ic_mem_req_ready),
        .mem_req_addr    (ic_mem_req_addr),
        .mem_resp_valid  (ic_mem_resp_valid),
        .mem_resp_data   (ic_mem_resp_data)
    );

    // ------------------------------------------------------------------
    // Branch predictor
    // ------------------------------------------------------------------
    branch_predictor u_bp (
        .clk            (clk),
        .rst_n          (rst_n),
        .lookup_valid   (bp_lookup_valid),
        .lookup_pc      (bp_lookup_pc),
        .hit            (bp_hit),
        .target         (bp_target),
        .pred_taken     (bp_pred_taken),
        .train_valid    (bp_train_valid),
        .train_pc       (bp_train_pc),
        .train_target   (bp_train_target),
        .train_taken    (bp_train_taken),
        .train_is_branch(bp_train_is_branch),
        .train_is_jal   (bp_train_is_jal),
        .train_is_jalr  (bp_train_is_jalr)
    );

    // ------------------------------------------------------------------
    // Control resolution (conservative: control resolves at ROB head)
    // ------------------------------------------------------------------
    assign control_resolve = int_issue_fire && iq_issue_control && (iq_issue_tag == rob_head);
    assign control_is_branch = control_resolve && (iq_issue_insn[6:0] == 7'b1100011);
    assign control_is_jal    = control_resolve && (iq_issue_insn[6:0] == 7'b1101111);
    assign control_is_jalr   = control_resolve && (iq_issue_insn[6:0] == 7'b1100111);

    assign control_mispredict = control_resolve && (alu_actual_next != iq_issue_prediction);

    assign redirect    = control_mispredict;
    assign redirect_pc = alu_actual_next;

    assign squash = redirect;

    assign bp_train_valid     = control_resolve;
    assign bp_train_pc        = iq_issue_pc;
    assign bp_train_target    = alu_taken_target;
    assign bp_train_taken     = alu_taken;
    assign bp_train_is_branch = control_is_branch;
    assign bp_train_is_jal    = control_is_jal;
    assign bp_train_is_jalr   = control_is_jalr;

    // ------------------------------------------------------------------
    // Bypass event: younger integer issue while older load waits on bus
    // ------------------------------------------------------------------
    assign bypass_event = int_issue_fire && lsu_wait_load &&
                          (dmem_valid && !dmem_ready);

    // ------------------------------------------------------------------
    // Sequential state
    // ------------------------------------------------------------------
    always_ff @(posedge clk) begin
        if (!rst_n) begin
            cur_epoch          <= 2'b00;
            halted_r           <= 1'b0;
            exit_code_r        <= 32'd0;
            last_imem_addr     <= 32'd0;
            fetch_pc           <= 32'd0;
            fetch_outstanding  <= 1'b0;
            fetch_pc_saved     <= 32'd0;
            fetch_pred_saved   <= 32'd0;
            fetch_epoch_saved  <= 2'b00;
            buf_valid          <= 1'b0;
            buf_pc             <= 32'd0;
            buf_insn           <= 32'd0;
            buf_pred           <= 32'd0;
            buf_epoch          <= 2'b00;
            ecall_pending      <= 1'b0;
        end else if (halted_r) begin
            // frozen
        end else begin
            // Fetch request acceptance
            if (fetch_accept) begin
                fetch_outstanding <= 1'b1;
                fetch_pc_saved    <= fetch_pc;
                fetch_pred_saved  <= pred_next_pc;
                fetch_epoch_saved <= cur_epoch;
                fetch_pc          <= pred_next_pc;
            end

            // Fetch response capture
            if (ic_resp_ok) begin
                fetch_outstanding <= 1'b0;
                buf_valid         <= 1'b1;
                buf_pc            <= fetch_pc_saved;
                buf_insn          <= ic_cpu_resp_data;
                buf_pred          <= fetch_pred_saved;
                buf_epoch         <= fetch_epoch_saved;
            end

            // Buffer consumption
            if (fetch_consume) begin
                buf_valid <= 1'b0;
            end

            // Squash clears buffer and outstanding fetch
            if (squash) begin
                buf_valid         <= 1'b0;
                fetch_outstanding <= 1'b0;
                fetch_pc          <= redirect_pc;
                cur_epoch         <= cur_epoch + 2'b01;
            end

            // ECALL pending
            if (dispatch_accept && d_ecall) begin
                ecall_pending <= 1'b1;
            end

            // ECALL commit -> halt next edge
            if (rob_commit_valid && rob_commit_ecall) begin
                halted_r    <= 1'b1;
                exit_code_r <= committed_x10;
            end

            // Track last external imem address
            if (ic_mem_req_valid && ic_mem_req_ready) begin
                last_imem_addr <= ic_mem_req_addr;
            end
        end
    end

    // ------------------------------------------------------------------
    // External bus outputs
    // ------------------------------------------------------------------
    assign imem_valid = rst_n && !halted_r && ic_mem_req_valid;
    assign imem_addr  = halted_r ? last_imem_addr : ic_mem_req_addr;
    assign ic_mem_req_ready = imem_ready;
    assign ic_mem_resp_valid = imem_valid && imem_ready;
    assign ic_mem_resp_data  = imem_rdata;

    assign dmem_valid = rst_n && !halted_r && dc_mem_req_valid;
    assign dmem_we    = dc_mem_req_we;
    assign dmem_addr  = dc_mem_req_addr;
    assign dmem_wdata = dc_mem_req_wdata;
    assign dmem_be    = dc_mem_req_be;
    assign dc_mem_req_ready = dmem_ready;
    assign dc_mem_resp_valid = dmem_valid && dmem_ready;
    assign dc_mem_resp_data  = dmem_rdata;

    // ------------------------------------------------------------------
    // Debug / capability outputs
    // ------------------------------------------------------------------
    assign dbg_features      = 3'b111;
    assign halted            = halted_r;
    assign exit_code         = exit_code_r;
    assign dbg_retire_valid  = rst_n && !halted_r && rob_commit_valid;
    assign dbg_retire_pc     = rob_commit_pc;
    assign dbg_retire_insn   = rob_commit_insn;
    assign dbg_bp_resolve    = rst_n && !halted_r && control_resolve;
    assign dbg_bp_mispredict = rst_n && !halted_r && control_mispredict;
    assign dbg_ooo_issue     = rst_n && !halted_r && (int_issue_fire || lsu_launch_fire);
    assign dbg_ooo_bypass    = rst_n && !halted_r && bypass_event;

endmodule
