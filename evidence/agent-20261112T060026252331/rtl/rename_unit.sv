module rename_unit (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [4:0]  rs1,
    input  logic [4:0]  rs2,
    input  logic [4:0]  rd,
    input  logic [4:0]  dbg_addr,
    input  logic        writes_rd,
    input  logic        dispatch,
    input  logic        checkpoint,
    input  logic [2:0]  dispatch_tag,
    input  logic [2:0]  recover_tag,
    input  logic        recover,
    input  logic        commit,
    input  logic        commit_writes,
    input  logic [4:0]  commit_rd,
    input  logic [5:0]  commit_prd,
    input  logic [5:0]  commit_old_prd,
    input  logic        alu_wb,
    input  logic        mem_wb,
    input  logic [5:0]  alu_prd,
    input  logic [5:0]  mem_prd,
    input  logic [31:0] alu_value,
    input  logic [31:0] mem_value,
    input  logic [5:0]  iq_rs1_idx,
    input  logic [5:0]  iq_rs2_idx,
    input  logic [5:0]  lsu_rs1_idx,
    input  logic [5:0]  lsu_rs2_idx,
    output logic [31:0] iq_rs1_value,
    output logic [31:0] iq_rs2_value,
    output logic [31:0] lsu_rs1_value,
    output logic [31:0] lsu_rs2_value,
    output logic [5:0]  prs1,
    output logic [5:0]  prs2,
    output logic [5:0]  new_prd,
    output logic [5:0]  old_prd,
    output logic        can_allocate,
    output logic [39:0] ready_bits,
    output logic [31:0] dbg_data,
    output logic [31:0] committed_x10
);

    localparam int NREG = 40;
    localparam int NFREE = 8;

    logic [5:0]  rat [0:31];
    logic [5:0]  cmap[0:31];
    logic [31:0] prf [0:NREG-1];
    logic [NREG-1:0] rdy;

    logic [5:0]  free_slot [0:NFREE-1];
    logic [3:0]  free_head;
    logic [3:0]  free_tail;

    logic [5:0]  ckpt_rat [0:7][0:31];
    logic [3:0]  ckpt_head[0:7];

    logic [3:0]  free_occ;
    logic [5:0]  alloc_pr;
    logic        alloc_ok;
    logic [5:0]  src1_pr;
    logic [5:0]  src2_pr;
    logic [5:0]  own_pr;
    logic [5:0]  own_old;
    logic        do_alloc;
    logic [3:0]  next_free_head;
    logic [3:0]  next_free_tail;
    logic [5:0]  next_free_slot;
    logic [5:0]  commit_slot;
    logic [31:0] v_iq1;
    logic [31:0] v_iq2;
    logic [31:0] v_lsu1;
    logic [31:0] v_lsu2;
    int          i;

    assign free_occ = free_tail - free_head;
    assign can_allocate = (free_occ != 4'd0);
    assign alloc_pr = free_slot[free_head[2:0]];
    assign alloc_ok = can_allocate && (alloc_pr != 6'd0);

    assign src1_pr = (rs1 == 5'd0) ? 6'd0 : rat[rs1];
    assign src2_pr = (rs2 == 5'd0) ? 6'd0 : rat[rs2];
    assign own_pr  = (writes_rd && (rd != 5'd0)) ? alloc_pr : 6'd0;
    assign own_old = (writes_rd && (rd != 5'd0)) ? rat[rd] : 6'd0;

    assign prs1 = src1_pr;
    assign prs2 = src2_pr;
    assign new_prd = own_pr;
    assign old_prd = own_old;

    assign do_alloc = dispatch && can_allocate && !recover && writes_rd && (rd != 5'd0);

    always_comb begin
        v_iq1  = prf[iq_rs1_idx];
        v_iq2  = prf[iq_rs2_idx];
        v_lsu1 = prf[lsu_rs1_idx];
        v_lsu2 = prf[lsu_rs2_idx];
        // No combinational ALU bypass into the LSU read ports: alu_value is
        // derived from the IQ read values, so feeding it back into the LSU
        // operands would close a loop through issue arbitration. Registered
        // PRF writeback plus registered PRF reads suffice.
        if (mem_wb && (mem_prd != 6'd0)) begin
            if (iq_rs1_idx == mem_prd) v_iq1 = mem_value;
            if (iq_rs2_idx == mem_prd) v_iq2 = mem_value;
            if (lsu_rs1_idx == mem_prd) v_lsu1 = mem_value;
            if (lsu_rs2_idx == mem_prd) v_lsu2 = mem_value;
        end
    end

    assign iq_rs1_value  = v_iq1;
    assign iq_rs2_value  = v_iq2;
    assign lsu_rs1_value = v_lsu1;
    assign lsu_rs2_value = v_lsu2;

    assign ready_bits = rdy;

    assign dbg_data = (dbg_addr == 5'd0) ? 32'd0 : prf[cmap[dbg_addr]];
    assign committed_x10 = (cmap[5'd10] == 6'd0) ? 32'd0 : prf[cmap[5'd10]];

    assign commit_slot = commit_old_prd;
    assign next_free_head = free_head + (do_alloc ? 4'd1 : 4'd0);
    assign next_free_tail = free_tail + ((commit && commit_writes && (commit_rd != 5'd0) && (commit_old_prd != 6'd0)) ? 4'd1 : 4'd0);
    assign next_free_slot = commit_old_prd;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            free_head <= 4'd0;
            free_tail <= 4'd8;
            for (i = 0; i < 32; i = i + 1) begin
                rat[i]  <= i[5:0];
                cmap[i] <= i[5:0];
            end
            for (i = 0; i < NREG; i = i + 1) begin
                prf[i] <= 32'd0;
                rdy[i] <= 1'b0;
            end
            for (i = 0; i < 32; i = i + 1) begin
                rdy[i] <= 1'b1;
            end
            rdy[0] <= 1'b1;
            for (i = 0; i < NFREE; i = i + 1) begin
                free_slot[i] <= 6'd32 + i[5:0];
            end
            for (i = 0; i < 8; i = i + 1) begin
                ckpt_head[i] <= 4'd0;
            end
        end else begin
            free_head <= next_free_head;
            free_tail <= next_free_tail;

            if (do_alloc) begin
                if (own_pr != 6'd0) begin
                    rat[rd] <= own_pr;
                    rdy[own_pr] <= 1'b0;
                end
            end

            if (commit && commit_writes && (commit_rd != 5'd0) && (commit_prd != 6'd0)) begin
                cmap[commit_rd] <= commit_prd;
            end

            if (commit && commit_writes && (commit_rd != 5'd0) && (commit_old_prd != 6'd0)) begin
                free_slot[free_tail[2:0]] <= next_free_slot;
            end

            if (alu_wb && (alu_prd != 6'd0)) begin
                prf[alu_prd] <= alu_value;
                rdy[alu_prd] <= 1'b1;
            end
            if (mem_wb && (mem_prd != 6'd0)) begin
                prf[mem_prd] <= mem_value;
                rdy[mem_prd] <= 1'b1;
            end

            if (checkpoint) begin
                for (i = 0; i < 32; i = i + 1) begin
                    ckpt_rat[dispatch_tag][i] <= rat[i];
                end
                if (do_alloc && (own_pr != 6'd0)) begin
                    ckpt_rat[dispatch_tag][rd] <= own_pr;
                end
                ckpt_head[dispatch_tag] <= next_free_head;
            end

            if (recover) begin
                for (i = 0; i < 32; i = i + 1) begin
                    rat[i] <= ckpt_rat[recover_tag][i];
                end
                free_head <= ckpt_head[recover_tag];
                // Preserve readiness of live mappings (including committed
                // sources). Only clear the ready bit of each newly allocated
                // destination, which is handled in the do_alloc branch above.
            end
        end
    end

endmodule
