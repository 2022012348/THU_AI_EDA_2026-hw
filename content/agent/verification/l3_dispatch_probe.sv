`timescale 1ns/1ps

module l3_rename_probe;
    logic clk = 0;
    always #5 clk = ~clk;
    logic rst_n = 0, dispatch = 0, writes_rd = 1;
    wire can_allocate;
    wire [5:0] new_prd;
    logic [7:0] seen = 0;
    logic unique_prs = 1;

    rename_unit dut (
        .clk(clk), .rst_n(rst_n), .rs1(5'd0), .rs2(5'd0), .rd(5'd1), .dbg_addr(5'd0),
        .writes_rd(writes_rd), .dispatch(dispatch), .checkpoint(1'b0), .dispatch_tag(3'd0),
        .recover_tag(3'd0), .recover(1'b0), .commit(1'b0), .commit_writes(1'b0),
        .commit_rd(5'd0), .commit_prd(6'd0), .commit_old_prd(6'd0),
        .alu_wb(1'b0), .mem_wb(1'b0), .alu_prd(6'd0), .mem_prd(6'd0),
        .alu_value(32'd0), .mem_value(32'd0), .iq_rs1_idx(6'd0), .iq_rs2_idx(6'd0),
        .lsu_rs1_idx(6'd0), .lsu_rs2_idx(6'd0), .new_prd(new_prd), .can_allocate(can_allocate)
    );

    initial begin
        repeat (2) @(negedge clk);
        rst_n = 1;
        dispatch = 1;
        writes_rd = 0;
        #1;
        $display("RENAME|case=nonwriter|prd=%0d|match=%b", new_prd, new_prd === 6'd0);
        @(negedge clk);
        writes_rd = 1;
        for (int index = 0; index < 8; index++) begin
            #1;
            if (can_allocate !== 1'b1 || (new_prd >= 6'd32 && new_prd <= 6'd39) !== 1'b1)
                unique_prs = 0;
            else if (seen[new_prd[2:0]])
                unique_prs = 0;
            else
                seen[new_prd[2:0]] = 1;
            $display("RENAME|case=allocate_%0d|ready=%b|prd=%0d|match=%b",
                     index, can_allocate, new_prd, unique_prs);
            @(negedge clk);
        end
        dispatch = 0;
        #1;
        $display("RENAME|case=full|ready=%b|match=%b", can_allocate, can_allocate === 1'b0);
        $finish;
    end
endmodule

module l3_iq_probe;
    logic clk = 0;
    always #5 clk = ~clk;
    logic rst_n = 0, enqueue = 0;
    logic [2:0] enq_tag = 0;
    logic [5:0] enq_prs1 = 0;
    logic [39:0] ready_bits = 40'hfeffffffff;
    wire issue_valid;
    wire [2:0] issue_tag;

    issue_queue dut (
        .clk(clk), .rst_n(rst_n), .squash(1'b0), .enqueue(enqueue), .disp_rob_idx(enq_tag), .rob_head(3'd6),
        .enq_pc(32'd0), .enq_insn(32'h00108093), .enq_prediction(32'd4), .enq_prs1(enq_prs1),
        .enq_prs2(6'd0), .enq_prd(6'd33), .enq_control(1'b0),
        .ready_bits(ready_bits), .issue_ready(1'b0), .issue_valid(issue_valid), .issue_tag(issue_tag)
    );

    initial begin
        repeat (2) @(negedge clk);
        rst_n = 1;
        enqueue = 1;
        enq_tag = 7;
        enq_prs1 = 32;
        @(negedge clk);
        enq_tag = 0;
        enq_prs1 = 0;
        @(negedge clk);
        enqueue = 0;
        #1;
        $display("IQ|case=ready_bypass|tag=%0d|match=%b", issue_tag,
                 issue_valid === 1'b1 && issue_tag === 3'd0);
        ready_bits[32] = 1;
        #1;
        $display("IQ|case=age_wrap|tag=%0d|match=%b", issue_tag,
                 issue_valid === 1'b1 && issue_tag === 3'd7);
        $finish;
    end
endmodule

module l3_lsu_probe;
    logic clk = 0;
    always #5 clk = ~clk;
    logic rst_n = 0, enqueue = 0, enq_store = 1, req_ready = 0, resp_valid = 0;
    logic [2:0] tag = 0, rob_head = 0;
    wire req_valid, req_we, complete_valid;
    wire [31:0] req_addr, req_wdata, complete_value;
    wire [3:0] req_be;
    wire [2:0] complete_tag;
    wire [5:0] complete_prd;
    logic stable, store_completed, load_completed;

    load_store_unit dut (
        .clk(clk), .rst_n(rst_n), .squash(1'b0), .enqueue(enqueue), .disp_rob_idx(tag), .rob_head(rob_head),
        .enq_prs1(6'd1), .enq_prs2(enq_store ? 6'd2 : 6'd0),
        .enq_prd(enq_store ? 6'd0 : 6'd32), .enq_imm(32'd1), .enq_funct3(3'd0), .enq_store(enq_store),
        .ready_bits(40'hffffffffff), .lsu_rs1_value(32'h100), .lsu_rs2_value(32'h89),
        .cpu_req_valid(req_valid), .cpu_req_we(req_we), .cpu_req_addr(req_addr),
        .cpu_req_wdata(req_wdata), .cpu_req_be(req_be), .cpu_req_ready(req_ready),
        .cpu_resp_valid(resp_valid), .cpu_resp_rdata(32'h12348978), .complete_valid(complete_valid),
        .complete_tag(complete_tag), .complete_prd(complete_prd), .complete_value(complete_value)
    );

    initial begin
        repeat (2) @(negedge clk);
        rst_n = 1;
        enqueue = 1;
        @(negedge clk);
        enqueue = 0;
        stable = 1;
        repeat (2) begin
            #1;
            if ({req_valid, req_we, req_addr, req_wdata, req_be} !==
                {1'b1, 1'b1, 32'h101, 32'h89898989, 4'b0010})
                stable = 0;
            @(negedge clk);
        end
        $display("LSU|case=store_wait|match=%b", stable);
        req_ready = 1;
        @(negedge clk);
        req_ready = 0;

        tag = 1;
        enq_store = 0;
        enqueue = 1;
        resp_valid = 1;
        #1;
        store_completed = complete_valid && complete_tag == 0 && complete_prd == 0;
        @(negedge clk);
        store_completed = store_completed || (complete_valid && complete_tag == 0 && complete_prd == 0);
        $display("LSU|case=store_completion|match=%b", store_completed === 1'b1);
        resp_valid = 0;
        enqueue = 0;
        rob_head = 1;
        #1;
        $display("LSU|case=load_after_response_enqueue|match=%b",
                 {req_valid, req_we, req_addr} === {1'b1, 1'b0, 32'h101});
        req_ready = 1;
        @(negedge clk);
        req_ready = 0;
        resp_valid = 1;
        #1;
        load_completed = complete_valid && complete_tag == 1 && complete_prd == 32 &&
                         complete_value == 32'hffffff89;
        @(negedge clk);
        load_completed = load_completed || (complete_valid && complete_tag == 1 && complete_prd == 32 &&
                                            complete_value == 32'hffffff89);
        $display("LSU|case=signed_load_completion|match=%b", load_completed === 1'b1);
        $finish;
    end
endmodule

module l3_rob_probe;
    logic clk = 0;
    always #5 clk = ~clk;
    logic rst_n = 0, alloc_valid = 0, alloc_ecall = 0, alu_complete = 0;
    logic [2:0] alu_tag = 0, first_tag;
    logic [31:0] alloc_pc = 32'h100;
    wire alloc_ready, commit_valid, commit_ecall;
    wire [2:0] alloc_rob_idx, head;
    wire [31:0] commit_pc;
    logic contiguous = 1;

    reorder_buffer dut (
        .clk(clk), .rst_n(rst_n), .alloc_valid(alloc_valid), .alloc_ready(alloc_ready),
        .alloc_rob_idx(alloc_rob_idx), .head(head), .alloc_pc(alloc_pc), .alloc_insn(32'h00000013),
        .alloc_rd(5'd0), .alloc_prd(6'd0), .alloc_old_prd(6'd0), .alloc_writes(1'b0), .alloc_ecall(alloc_ecall),
        .alu_complete(alu_complete), .mem_complete(1'b0), .recover(1'b0),
        .alu_tag(alu_tag), .mem_tag(3'd0), .commit_valid(commit_valid), .commit_pc(commit_pc),
        .commit_ecall(commit_ecall)
    );

    initial begin
        repeat (2) @(negedge clk);
        rst_n = 1;
        #1;
        first_tag = alloc_rob_idx;
        repeat (3) @(negedge clk);
        $display("ROB|case=idle|tag=%0d|match=%b", alloc_rob_idx,
                 alloc_ready === 1'b1 && alloc_rob_idx === first_tag && head === first_tag);
        alloc_valid = 1;
        for (int index = 0; index < 8; index++) begin
            #1;
            if (alloc_ready !== 1'b1 || alloc_rob_idx !== (first_tag + index[2:0]))
                contiguous = 0;
            @(negedge clk);
            alloc_pc = alloc_pc + 4;
        end
        alloc_valid = 0;
        $display("ROB|case=full|ready=%b|match=%b", alloc_ready, contiguous && alloc_ready === 1'b0);
        alu_tag = first_tag + 3'd1;
        alu_complete = 1;
        @(negedge clk);
        $display("ROB|case=younger_complete|commit=%b|match=%b", commit_valid, commit_valid === 1'b0);
        alu_tag = first_tag;
        @(negedge clk);
        $display("ROB|case=head_complete|pc=%h|match=%b", commit_pc,
                 commit_valid === 1'b1 && commit_pc === 32'h100);
        alu_complete = 0;
        @(negedge clk);
        $display("ROB|case=next_head|pc=%h|match=%b", commit_pc,
                 alloc_ready === 1'b1 && commit_valid === 1'b1 && commit_pc === 32'h104);
        rst_n = 0;
        @(negedge clk);
        rst_n = 1;
        alloc_pc = 32'h200;
        alloc_ecall = 1;
        alloc_valid = 1;
        @(negedge clk);
        alloc_valid = 0;
        alloc_ecall = 0;
        #1;
        $display("ROB|case=ecall_complete|commit=%b|ecall=%b|match=%b", commit_valid, commit_ecall,
                 commit_valid === 1'b1 && commit_ecall === 1'b1 && commit_pc === 32'h200);
        $finish;
    end
endmodule
