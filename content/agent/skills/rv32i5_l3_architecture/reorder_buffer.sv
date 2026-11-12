module reorder_buffer (
    input logic clk, rst_n, alloc_valid,
    output logic alloc_ready,
    output logic [2:0] alloc_rob_idx, head,
    input logic [31:0] alloc_pc, alloc_insn,
    input logic [4:0] alloc_rd,
    input logic [5:0] alloc_prd, alloc_old_prd,
    input logic alloc_writes, alloc_ecall,
    input logic alu_complete, mem_complete, recover,
    input logic [2:0] alu_tag, mem_tag,
    output logic commit_valid,
    output logic [31:0] commit_pc, commit_insn,
    output logic [4:0] commit_rd,
    output logic [5:0] commit_prd, commit_old_prd,
    output logic commit_writes, commit_ecall
);
    logic [2:0] tail;
    logic [3:0] count;
    logic [7:0] done;
    logic [31:0] pc [0:7], insn [0:7];
    logic [4:0] rd [0:7];
    logic [5:0] prd [0:7], old_prd [0:7];
    logic writes [0:7], ecall [0:7];

    assign alloc_ready = count < 4'd8;
    assign alloc_rob_idx = tail;
    assign commit_valid = count != 0 && done[head] && !recover;
    assign commit_pc = pc[head];
    assign commit_insn = insn[head];
    assign commit_rd = rd[head];
    assign commit_prd = prd[head];
    assign commit_old_prd = old_prd[head];
    assign commit_writes = writes[head];
    assign commit_ecall = ecall[head];

    always @(posedge clk) begin
        if (!rst_n) begin
            head <= 0;
            tail <= 0;
            count <= 0;
            done <= 0;
        end else if (recover) begin
            // Only the oldest control instruction may resolve. Keep it.
            tail <= head + 3'd1;
            count <= 4'd1;
            done <= 8'b1 << head;
        end else begin
            case ({alloc_valid && alloc_ready, commit_valid})
                2'b10: count <= count + 4'd1;
                2'b01: count <= count - 4'd1;
                default: ;
            endcase
            if (alu_complete) done[alu_tag] <= 1;
            if (mem_complete) done[mem_tag] <= 1;
            if (commit_valid) begin
                done[head] <= 0;
                head <= head + 3'd1;
            end
            if (alloc_valid && alloc_ready) begin
                pc[tail] <= alloc_pc;
                insn[tail] <= alloc_insn;
                rd[tail] <= alloc_rd;
                prd[tail] <= alloc_prd;
                old_prd[tail] <= alloc_old_prd;
                writes[tail] <= alloc_writes;
                ecall[tail] <= alloc_ecall;
                done[tail] <= alloc_ecall;
                tail <= tail + 3'd1;
            end
        end
    end
endmodule
