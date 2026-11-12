module issue_queue (
    input logic clk, rst_n, squash, enqueue,
    input logic [2:0] disp_rob_idx, rob_head,
    input logic [31:0] enq_pc, enq_insn, enq_prediction,
    input logic [5:0] enq_prs1, enq_prs2, enq_prd,
    input logic enq_control,
    input logic [39:0] ready_bits,
    input logic issue_ready,
    output logic issue_valid,
    output logic [2:0] issue_tag,
    output logic [31:0] issue_pc, issue_insn, issue_prediction,
    output logic [5:0] iq_rs1_idx, iq_rs2_idx, issue_prd,
    output logic issue_control
);
    logic [7:0] valid;
    logic [31:0] pc [0:7], insn [0:7], prediction [0:7];
    logic [5:0] prs1 [0:7], prs2 [0:7], prd [0:7];
    logic control [0:7];
    logic [2:0] tag;
    integer offset;

    always @* begin
        issue_valid = 0;
        issue_tag = rob_head;
        for (offset = 0; offset < 8; offset = offset + 1) begin
            tag = rob_head + offset[2:0];
            if (!issue_valid && valid[tag] && ready_bits[prs1[tag]] && ready_bits[prs2[tag]]
                    && (!control[tag] || tag == rob_head)) begin
                issue_valid = 1;
                issue_tag = tag;
            end
        end
    end
    assign issue_pc = pc[issue_tag];
    assign issue_insn = insn[issue_tag];
    assign issue_prediction = prediction[issue_tag];
    assign iq_rs1_idx = issue_valid ? prs1[issue_tag] : 6'd0;
    assign iq_rs2_idx = issue_valid ? prs2[issue_tag] : 6'd0;
    assign issue_prd = issue_valid ? prd[issue_tag] : 6'd0;
    assign issue_control = issue_valid && control[issue_tag];

    always @(posedge clk) begin
        if (!rst_n || squash) begin
            valid <= 0;
        end else begin
            if (issue_valid && issue_ready) valid[issue_tag] <= 0;
            if (enqueue) begin
                valid[disp_rob_idx] <= 1;
                pc[disp_rob_idx] <= enq_pc;
                insn[disp_rob_idx] <= enq_insn;
                prediction[disp_rob_idx] <= enq_prediction;
                prs1[disp_rob_idx] <= enq_prs1;
                prs2[disp_rob_idx] <= enq_prs2;
                prd[disp_rob_idx] <= enq_prd;
                control[disp_rob_idx] <= enq_control;
            end
        end
    end
endmodule
