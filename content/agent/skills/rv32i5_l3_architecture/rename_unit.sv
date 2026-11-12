module rename_unit (
    input logic clk, rst_n,
    input logic [4:0] rs1, rs2, rd, dbg_addr,
    input logic writes_rd, dispatch, checkpoint,
    input logic [2:0] dispatch_tag, recover_tag,
    input logic recover, commit, commit_writes,
    input logic [4:0] commit_rd,
    input logic [5:0] commit_prd, commit_old_prd,
    input logic alu_wb, mem_wb,
    input logic [5:0] alu_prd, mem_prd,
    input logic [31:0] alu_value, mem_value,
    input logic [5:0] iq_rs1_idx, iq_rs2_idx, lsu_rs1_idx, lsu_rs2_idx,
    output logic [31:0] iq_rs1_value, iq_rs2_value, lsu_rs1_value, lsu_rs2_value,
    output logic [5:0] prs1, prs2, new_prd, old_prd,
    output logic can_allocate,
    output logic [39:0] ready_bits,
    output logic [31:0] dbg_data, committed_x10
);
    logic [5:0] speculative [0:31];
    logic [5:0] committed [0:31];
    logic [31:0] prf [0:39];
    logic [5:0] free_list [0:7];
    logic [3:0] free_head, free_tail;
    logic [3:0] saved_free_head [0:7];
    logic [7:0] saved_allocates;
    logic [5:0] saved_rat [0:7][0:31];
    logic [5:0] saved_old_prd [0:7];
    logic [39:0] recovery_free;
    integer r, c, x;

    assign prs1 = speculative[rs1];
    assign prs2 = speculative[rs2];
    assign old_prd = speculative[rd];
    assign iq_rs1_value = prf[iq_rs1_idx];
    assign iq_rs2_value = prf[iq_rs2_idx];
    assign lsu_rs1_value = prf[lsu_rs1_idx];
    assign lsu_rs2_value = prf[lsu_rs2_idx];
    assign dbg_data = prf[committed[dbg_addr]];
    assign committed_x10 = prf[committed[10]];

    assign can_allocate = !writes_rd || free_head != free_tail;
    assign new_prd = writes_rd && can_allocate ? free_list[free_head[2:0]] : 6'd0;

    // A resolving control instruction is at ROB head. Its checkpoint owns
    // the architectural mappings plus its saved destination, if any.
    always @* begin
        recovery_free = {40{1'b1}};
        for (r = 0; r < 32; r = r + 1)
            recovery_free[saved_rat[recover_tag][r]] = 1'b0;
        recovery_free[saved_old_prd[recover_tag]] = 1'b0;
        recovery_free[0] = 1'b0;
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            free_head <= 0;
            free_tail <= 4'd8;
            saved_allocates <= 0;
            ready_bits <= 40'h00ffffffff;
            for (x = 0; x < 40; x = x + 1) prf[x] <= 0;
            for (x = 0; x < 32; x = x + 1) begin
                speculative[x] <= x[5:0];
                committed[x] <= x[5:0];
            end
            for (c = 0; c < 8; c = c + 1) begin
                free_list[c] <= 6'd32 + c[5:0];
                saved_free_head[c] <= 0;
                saved_old_prd[c] <= 0;
                for (x = 0; x < 32; x = x + 1) saved_rat[c][x] <= x[5:0];
            end
        end else begin
            if (recover) begin
                // Preserve the resolving instruction's allocation. Older
                // commits already appended their releases to the current tail.
                free_head <= saved_free_head[recover_tag] + {3'b000, saved_allocates[recover_tag]};
                ready_bits <= ready_bits & ~recovery_free;
                for (x = 0; x < 32; x = x + 1)
                    speculative[x] <= saved_rat[recover_tag][x];
            end else if (dispatch) begin
                if (writes_rd) begin
                    speculative[rd] <= new_prd;
                    free_head <= free_head + 4'd1;
                    ready_bits[new_prd] <= 0;
                end
                if (checkpoint) begin
                    saved_free_head[dispatch_tag] <= free_head;
                    saved_allocates[dispatch_tag] <= writes_rd;
                    saved_old_prd[dispatch_tag] <= writes_rd ? old_prd : 6'd0;
                    for (x = 0; x < 32; x = x + 1)
                        saved_rat[dispatch_tag][x] <= writes_rd && rd == x ? new_prd : speculative[x];
                end
            end
            if (alu_wb && alu_prd != 0) begin
                prf[alu_prd] <= alu_value;
                ready_bits[alu_prd] <= 1;
            end
            if (mem_wb && mem_prd != 0) begin
                prf[mem_prd] <= mem_value;
                ready_bits[mem_prd] <= 1;
            end
            if (commit && commit_writes) begin
                committed[commit_rd] <= commit_prd;
                if (commit_old_prd != 0) begin
                    free_list[free_tail[2:0]] <= commit_old_prd;
                    free_tail <= free_tail + 4'd1;
                    ready_bits[commit_old_prd] <= 0;
                end
            end
        end
    end
endmodule
