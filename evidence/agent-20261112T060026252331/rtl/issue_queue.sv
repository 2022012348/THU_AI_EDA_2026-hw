module issue_queue (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        squash,
    input  logic        enqueue,
    input  logic [2:0]  disp_rob_idx,
    input  logic [2:0]  rob_head,
    input  logic [31:0] enq_pc,
    input  logic [31:0] enq_insn,
    input  logic [31:0] enq_prediction,
    input  logic [5:0]  enq_prs1,
    input  logic [5:0]  enq_prs2,
    input  logic [5:0]  enq_prd,
    input  logic        enq_control,
    input  logic [39:0] ready_bits,
    input  logic        issue_ready,
    output logic        issue_valid,
    output logic [2:0]  issue_tag,
    output logic [31:0] issue_pc,
    output logic [31:0] issue_insn,
    output logic [31:0] issue_prediction,
    output logic [5:0]  iq_rs1_idx,
    output logic [5:0]  iq_rs2_idx,
    output logic [5:0]  issue_prd,
    output logic        issue_control
);
    logic        ent_valid [0:7];
    logic [31:0] ent_pc [0:7];
    logic [31:0] ent_insn [0:7];
    logic [31:0] ent_pred [0:7];
    logic [5:0]  ent_prs1 [0:7];
    logic [5:0]  ent_prs2 [0:7];
    logic [5:0]  ent_prd [0:7];
    logic        ent_ctrl [0:7];

    logic        best_found;
    logic [2:0]  best_idx;
    logic [3:0]  best_dist;
    logic [3:0]  cand_dist;
    logic        best_ready;
    logic        best_is_head;
    logic        best_ctrl;
    logic        best_ok;
    logic        issue_fire;
    logic [3:0]  i;
    logic [3:0]  j;

    always_comb begin
        best_found = 1'b0;
        best_idx = 3'b0;
        best_dist = 4'b0;
        cand_dist = 4'b0;
        best_ready = 1'b0;
        best_is_head = 1'b0;
        best_ctrl = 1'b0;
        best_ok = 1'b0;
        for (i = 4'd0; i < 4'd8; i = i + 4'd1) begin
            if (ent_valid[i]) begin
                cand_dist = {1'b0, i[2:0]} - {1'b0, rob_head};
                if (ready_bits[ent_prs1[i[2:0]]] &&
                    ready_bits[ent_prs2[i[2:0]]] &&
                    (!ent_ctrl[i] || (i[2:0] == rob_head))) begin
                    if (!best_found || (cand_dist < best_dist)) begin
                        best_found = 1'b1;
                        best_idx = i[2:0];
                        best_dist = cand_dist;
                    end
                end
            end
        end
        if (best_found) begin
            best_ready = ready_bits[ent_prs1[best_idx]] &&
                         ready_bits[ent_prs2[best_idx]];
            best_is_head = (best_idx == rob_head);
            best_ctrl = ent_ctrl[best_idx];
            best_ok = best_ready && (!best_ctrl || best_is_head);
        end
    end

    assign issue_valid = best_ok;
    assign issue_tag = best_idx;
    assign issue_pc = ent_pc[best_idx];
    assign issue_insn = ent_insn[best_idx];
    assign issue_prediction = ent_pred[best_idx];
    assign iq_rs1_idx = ent_prs1[best_idx];
    assign iq_rs2_idx = ent_prs2[best_idx];
    assign issue_prd = ent_prd[best_idx];
    assign issue_control = ent_ctrl[best_idx];

    assign issue_fire = issue_valid && issue_ready;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            for (j = 4'd0; j < 4'd8; j = j + 4'd1) begin
                ent_valid[j] <= 1'b0;
                ent_pc[j] <= 32'b0;
                ent_insn[j] <= 32'b0;
                ent_pred[j] <= 32'b0;
                ent_prs1[j] <= 6'b0;
                ent_prs2[j] <= 6'b0;
                ent_prd[j] <= 6'b0;
                ent_ctrl[j] <= 1'b0;
            end
        end else begin
            if (squash) begin
                for (j = 4'd0; j < 4'd8; j = j + 4'd1) begin
                    if (ent_valid[j] && (j[2:0] != rob_head)) begin
                        ent_valid[j] <= 1'b0;
                    end
                end
            end
            if (issue_fire) begin
                ent_valid[best_idx] <= 1'b0;
            end
            if (enqueue && !squash) begin
                ent_valid[disp_rob_idx] <= 1'b1;
                ent_pc[disp_rob_idx] <= enq_pc;
                ent_insn[disp_rob_idx] <= enq_insn;
                ent_pred[disp_rob_idx] <= enq_prediction;
                ent_prs1[disp_rob_idx] <= enq_prs1;
                ent_prs2[disp_rob_idx] <= enq_prs2;
                ent_prd[disp_rob_idx] <= enq_prd;
                ent_ctrl[disp_rob_idx] <= enq_control;
            end
        end
    end
endmodule
