module rv32i5 (
    input logic clk, rst_n,
    output logic [31:0] imem_addr,
    input logic [31:0] imem_rdata,
    output logic imem_valid,
    input logic imem_ready,
    output logic dmem_valid, dmem_we,
    output logic [31:0] dmem_addr, dmem_wdata,
    output logic [3:0] dmem_be,
    input logic [31:0] dmem_rdata,
    input logic dmem_ready,
    input logic [4:0] dbg_reg_addr,
    output logic [31:0] dbg_reg_data,
    output logic halted,
    output logic [31:0] exit_code,
    output logic [2:0] dbg_features,
    output logic dbg_retire_valid,
    output logic [31:0] dbg_retire_pc, dbg_retire_insn,
    output logic dbg_bp_resolve, dbg_bp_mispredict, dbg_ooo_issue, dbg_ooo_bypass
);
    localparam [6:0] OP_BRANCH=7'h63, OP_JAL=7'h6f, OP_JALR=7'h67;
    logic [31:0] fetch_pc, saved_pc, saved_prediction;
    logic [31:0] buf_pc, buf_insn, buf_prediction;
    logic [1:0] fetch_epoch, saved_epoch;
    logic fetching, buf_valid, halt_pending, halt_commit;
    logic [31:0] last_iaddr;
    logic i_ready, i_resp, i_req, i_bus_valid;
    logic [31:0] i_word, i_bus_addr;
    logic bp_hit, bp_taken;
    logic [31:0] bp_target, predicted_pc;

    logic dispatch, recover, alloc_ready, lsu_ready, can_allocate;
    logic [2:0] alloc_rob_idx, rob_head;
    logic d_load, d_store, d_control, d_ecall, d_writes;
    logic [4:0] dec_rs1, dec_rs2, dec_rd;
    logic [31:0] memory_imm;
    logic [5:0] dec_prs1, dec_prs2, dec_prd, dec_old_prd;

    logic issue_valid, issue_ready, issue_fire, issue_control;
    logic [2:0] issue_tag;
    logic [31:0] issue_pc, issue_insn, issue_prediction;
    logic [5:0] iq_rs1_idx, iq_rs2_idx, lsu_rs1_idx, lsu_rs2_idx;
    logic [5:0] issue_prd, mem_prd;
    logic [31:0] iq_rs1_value, iq_rs2_value, lsu_rs1_value, lsu_rs2_value;
    logic [31:0] alu_value, actual_next, taken_target;
    logic taken;
    logic [39:0] pr_ready;

    logic mem_complete, mem_wait_load, d_req, d_we, d_ready, d_resp;
    logic [2:0] mem_tag;
    logic [31:0] mem_value, d_addr, d_data, d_response;
    logic [3:0] d_be;
    logic d_bus_valid, d_bus_we;
    logic [31:0] d_bus_addr, d_bus_data;
    logic [3:0] d_bus_be;

    logic commit_valid, commit_writes, commit_ecall;
    logic [31:0] commit_pc, commit_insn, x10;
    logic [4:0] commit_rd;
    logic [5:0] commit_prd, commit_old_prd;

    assign dbg_features = 3'b111;
    decode u_decode (
        .buf_insn(buf_insn), .d_load(d_load), .d_store(d_store), .d_control(d_control),
        .d_ecall(d_ecall), .d_writes(d_writes), .dec_rs1(dec_rs1), .dec_rs2(dec_rs2),
        .dec_rd(dec_rd), .memory_imm(memory_imm)
    );
    assign dispatch = rst_n && !halted && !halt_pending && !recover && buf_valid
                      && alloc_ready && can_allocate && (!(d_load || d_store) || lsu_ready);

    assign i_req = rst_n && !halted && !halt_pending && !recover && !fetching && !buf_valid;
    assign predicted_pc = bp_hit && bp_taken ? bp_target : fetch_pc + 32'd4;
    assign imem_valid = rst_n && !halted && i_bus_valid;
    assign imem_addr = !rst_n ? 32'd0 : halted ? last_iaddr : i_bus_addr;
    assign dmem_valid = rst_n && !halted && d_bus_valid;
    assign dmem_we = d_bus_we;
    assign dmem_addr = d_bus_addr;
    assign dmem_wdata = d_bus_data;
    assign dmem_be = d_bus_be;

    icache u_icache (
        .clk(clk), .rst_n(rst_n), .cpu_req_valid(i_req), .cpu_req_addr(fetch_pc),
        .cpu_req_ready(i_ready), .cpu_resp_valid(i_resp), .cpu_resp_data(i_word),
        .flush(recover), .flush_epoch(fetch_epoch), .mem_req_valid(i_bus_valid),
        .mem_req_ready(imem_ready), .mem_req_addr(i_bus_addr),
        .mem_resp_valid(imem_valid && imem_ready), .mem_resp_data(imem_rdata)
    );
    dcache u_dcache (
        .clk(clk), .rst_n(rst_n), .cpu_req_valid(d_req), .cpu_req_we(d_we),
        .cpu_req_addr(d_addr), .cpu_req_wdata(d_data), .cpu_req_be(d_be),
        .cpu_req_ready(d_ready), .cpu_resp_valid(d_resp), .cpu_resp_rdata(d_response),
        .flush(1'b0), .flush_epoch(2'b0), .mem_req_valid(d_bus_valid),
        .mem_req_ready(dmem_ready), .mem_req_addr(d_bus_addr), .mem_req_we(d_bus_we),
        .mem_req_wdata(d_bus_data), .mem_req_be(d_bus_be),
        .mem_resp_valid(dmem_valid && dmem_ready && !dmem_we), .mem_resp_data(dmem_rdata)
    );
    branch_predictor u_bp (
        .clk(clk), .rst_n(rst_n), .lookup_valid(1'b1), .lookup_pc(fetch_pc),
        .hit(bp_hit), .target(bp_target), .pred_taken(bp_taken),
        .train_valid(issue_fire && issue_control), .train_pc(issue_pc), .train_target(taken_target),
        .train_taken(taken), .train_is_branch(issue_insn[6:0] == OP_BRANCH),
        .train_is_jal(issue_insn[6:0] == OP_JAL), .train_is_jalr(issue_insn[6:0] == OP_JALR)
    );
    rename_unit u_rename (
        .clk(clk), .rst_n(rst_n), .rs1(dec_rs1), .rs2(dec_rs2), .rd(dec_rd), .dbg_addr(dbg_reg_addr),
        .writes_rd(d_writes), .dispatch(dispatch), .checkpoint(d_control), .dispatch_tag(alloc_rob_idx),
        .recover_tag(issue_tag), .recover(recover), .commit(commit_valid), .commit_writes(commit_writes),
        .commit_rd(commit_rd), .commit_prd(commit_prd), .commit_old_prd(commit_old_prd),
        .alu_wb(issue_fire), .mem_wb(mem_complete), .alu_prd(issue_prd), .mem_prd(mem_prd),
        .alu_value(alu_value), .mem_value(mem_value),
        .iq_rs1_idx(iq_rs1_idx), .iq_rs2_idx(iq_rs2_idx), .lsu_rs1_idx(lsu_rs1_idx), .lsu_rs2_idx(lsu_rs2_idx),
        .iq_rs1_value(iq_rs1_value), .iq_rs2_value(iq_rs2_value),
        .lsu_rs1_value(lsu_rs1_value), .lsu_rs2_value(lsu_rs2_value),
        .prs1(dec_prs1), .prs2(dec_prs2), .new_prd(dec_prd), .old_prd(dec_old_prd),
        .can_allocate(can_allocate), .ready_bits(pr_ready), .dbg_data(dbg_reg_data), .committed_x10(x10)
    );
    issue_queue u_iq (
        .clk(clk), .rst_n(rst_n), .squash(recover), .enqueue(dispatch && !d_load && !d_store && !d_ecall),
        .disp_rob_idx(alloc_rob_idx), .rob_head(rob_head), .enq_pc(buf_pc), .enq_insn(buf_insn),
        .enq_prediction(buf_prediction), .enq_prs1(dec_prs1), .enq_prs2(dec_prs2), .enq_prd(dec_prd),
        .enq_control(d_control), .ready_bits(pr_ready), .issue_ready(issue_ready), .issue_valid(issue_valid),
        .issue_tag(issue_tag), .issue_pc(issue_pc), .issue_insn(issue_insn), .issue_prediction(issue_prediction),
        .iq_rs1_idx(iq_rs1_idx), .iq_rs2_idx(iq_rs2_idx), .issue_prd(issue_prd), .issue_control(issue_control)
    );
    reorder_buffer u_rob (
        .clk(clk), .rst_n(rst_n), .alloc_valid(dispatch), .alloc_ready(alloc_ready),
        .alloc_rob_idx(alloc_rob_idx), .head(rob_head), .alloc_pc(buf_pc), .alloc_insn(buf_insn),
        .alloc_rd(dec_rd), .alloc_prd(dec_prd), .alloc_old_prd(dec_old_prd), .alloc_writes(d_writes),
        .alloc_ecall(d_ecall), .alu_complete(issue_fire), .mem_complete(mem_complete),
        .recover(recover), .alu_tag(issue_tag), .mem_tag(mem_tag),
        .commit_valid(commit_valid), .commit_pc(commit_pc), .commit_insn(commit_insn),
        .commit_rd(commit_rd), .commit_prd(commit_prd), .commit_old_prd(commit_old_prd),
        .commit_writes(commit_writes), .commit_ecall(commit_ecall)
    );
    load_store_unit u_lsu (
        .clk(clk), .rst_n(rst_n), .squash(recover), .enqueue(dispatch && (d_load || d_store)),
        .disp_rob_idx(alloc_rob_idx), .rob_head(rob_head), .enq_prs1(dec_prs1), .enq_prs2(dec_prs2),
        .enq_prd(dec_prd), .enq_imm(memory_imm), .enq_funct3(buf_insn[14:12]), .enq_store(d_store),
        .ready_bits(pr_ready), .lsu_rs1_idx(lsu_rs1_idx), .lsu_rs2_idx(lsu_rs2_idx),
        .lsu_rs1_value(lsu_rs1_value), .lsu_rs2_value(lsu_rs2_value), .enq_ready(lsu_ready),
        .cpu_req_valid(d_req), .cpu_req_we(d_we), .cpu_req_addr(d_addr), .cpu_req_wdata(d_data),
        .cpu_req_be(d_be), .cpu_req_ready(d_ready), .cpu_resp_valid(d_resp), .cpu_resp_rdata(d_response),
        .complete_valid(mem_complete), .wait_load(mem_wait_load), .complete_tag(mem_tag),
        .complete_prd(mem_prd), .complete_value(mem_value)
    );

    assign issue_ready = rst_n && !halted && !(d_req && d_ready);
    assign issue_fire = issue_valid && issue_ready;
    assign recover = issue_fire && issue_control && actual_next != issue_prediction;
    alu u_alu (
        .issue_pc(issue_pc), .issue_insn(issue_insn),
        .iq_rs1_value(iq_rs1_value), .iq_rs2_value(iq_rs2_value),
        .alu_value(alu_value), .actual_next(actual_next), .taken_target(taken_target), .taken(taken)
    );

    always @(posedge clk) begin
        if (!rst_n) begin
            fetch_pc <= 0;
            saved_pc <= 0;
            saved_prediction <= 0;
            saved_epoch <= 0;
            fetch_epoch <= 0;
            fetching <= 0;
            buf_valid <= 0;
            buf_pc <= 0;
            buf_insn <= 0;
            buf_prediction <= 0;
            halt_pending <= 0;
            halt_commit <= 0;
            last_iaddr <= 0;
            halted <= 0;
            exit_code <= 0;
            dbg_retire_valid <= 0;
            dbg_retire_pc <= 0;
            dbg_retire_insn <= 0;
            dbg_bp_resolve <= 0;
            dbg_bp_mispredict <= 0;
            dbg_ooo_issue <= 0;
            dbg_ooo_bypass <= 0;
        end else begin
            dbg_retire_valid <= commit_valid && !halted;
            if (commit_valid && !halted) begin
                dbg_retire_pc <= commit_pc;
                dbg_retire_insn <= commit_insn;
            end
            dbg_bp_resolve <= issue_fire && issue_control;
            dbg_bp_mispredict <= recover;
            dbg_ooo_issue <= issue_fire || (d_req && d_ready && !halted);
            dbg_ooo_bypass <= issue_fire && !issue_control && issue_tag != rob_head
                              && mem_wait_load && dmem_valid && !dmem_ready;
            if (!halted) last_iaddr <= imem_addr;
            if (commit_valid && commit_ecall) halt_commit <= 1;
            if (halt_commit && !i_bus_valid && !d_bus_valid && !halted) begin
                halted <= 1;
                exit_code <= x10;
            end

            if (recover) begin
                fetch_pc <= actual_next;
                fetch_epoch <= fetch_epoch + 2'd1;
                fetching <= 0;
                buf_valid <= 0;
                halt_pending <= 0;
            end else begin
                if (i_req && i_ready) begin
                    fetching <= 1;
                    saved_pc <= fetch_pc;
                    saved_prediction <= predicted_pc;
                    saved_epoch <= fetch_epoch;
                    fetch_pc <= predicted_pc;
                end
                if (i_resp && fetching) begin
                    fetching <= 0;
                    if (saved_epoch == fetch_epoch) begin
                        buf_pc <= saved_pc;
                        buf_insn <= i_word;
                        buf_prediction <= saved_prediction;
                        buf_valid <= 1;
                    end
                end
                if (dispatch) begin
                    buf_valid <= 0;
                    if (d_ecall) halt_pending <= 1;
                end
            end
        end
    end
endmodule
