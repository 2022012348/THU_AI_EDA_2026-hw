module reorder_buffer (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        alloc_valid,
    output logic        alloc_ready,
    output logic [2:0]  alloc_rob_idx,
    output logic [2:0]  head,
    input  logic [31:0] alloc_pc,
    input  logic [31:0] alloc_insn,
    input  logic [4:0]  alloc_rd,
    input  logic [5:0]  alloc_prd,
    input  logic [5:0]  alloc_old_prd,
    input  logic        alloc_writes,
    input  logic        alloc_ecall,
    input  logic        alu_complete,
    input  logic        mem_complete,
    input  logic        recover,
    input  logic [2:0]  alu_tag,
    input  logic [2:0]  mem_tag,
    output logic        commit_valid,
    output logic [31:0] commit_pc,
    output logic [31:0] commit_insn,
    output logic [4:0]  commit_rd,
    output logic [5:0]  commit_prd,
    output logic [5:0]  commit_old_prd,
    output logic        commit_writes,
    output logic        commit_ecall
);

    localparam int DEPTH = 8;

    logic [2:0]  tail;
    logic [3:0]  count;

    logic        vld   [0:DEPTH-1];
    logic        done  [0:DEPTH-1];
    logic [31:0] pc_q  [0:DEPTH-1];
    logic [31:0] insn_q[0:DEPTH-1];
    logic [4:0]  rd_q  [0:DEPTH-1];
    logic [5:0]  prd_q [0:DEPTH-1];
    logic [5:0]  old_q [0:DEPTH-1];
    logic        wr_q  [0:DEPTH-1];
    logic        ec_q  [0:DEPTH-1];

    logic        commit_now;
    logic        alloc_now;
    logic [2:0]  next_head;
    logic [2:0]  next_tail;
    logic [3:0]  next_count;
    logic [3:0]  count_delta;
    int          i;

    assign alloc_rob_idx = tail;
    assign alloc_ready   = (count != 4'd8);

    assign commit_valid  = vld[head] && done[head];
    assign commit_pc     = pc_q[head];
    assign commit_insn   = insn_q[head];
    assign commit_rd     = rd_q[head];
    assign commit_prd    = prd_q[head];
    assign commit_old_prd= old_q[head];
    assign commit_writes = wr_q[head];
    assign commit_ecall  = ec_q[head];

    assign commit_now = commit_valid;
    assign alloc_now  = alloc_valid && alloc_ready && !recover;

    always_comb begin
        next_head  = head;
        next_tail  = tail;
        count_delta = 4'd0;
        if (commit_now) begin
            next_head = head + 3'd1;
            count_delta = count_delta - 4'd1;
        end
        if (recover) begin
            next_tail  = head + 3'd1;
            next_count = 4'd1;
        end else begin
            if (alloc_now) begin
                next_tail = tail + 3'd1;
                count_delta = count_delta + 4'd1;
            end
            next_count = count + count_delta;
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            head  <= 3'd0;
            tail  <= 3'd0;
            count <= 4'd0;
            for (i = 0; i < DEPTH; i = i + 1) begin
                vld[i]   <= 1'b0;
                done[i]  <= 1'b0;
                pc_q[i]  <= 32'd0;
                insn_q[i]<= 32'd0;
                rd_q[i]  <= 5'd0;
                prd_q[i] <= 6'd0;
                old_q[i] <= 6'd0;
                wr_q[i]  <= 1'b0;
                ec_q[i]  <= 1'b0;
            end
        end else begin
            head  <= next_head;
            tail  <= next_tail;
            count <= next_count;

            if (recover) begin
                for (i = 0; i < DEPTH; i = i + 1) begin
                    if (i != head) begin
                        vld[i]  <= 1'b0;
                        done[i] <= 1'b0;
                    end
                end
                done[head] <= 1'b1;
            end else begin
                if (commit_now) begin
                    vld[head]  <= 1'b0;
                    done[head] <= 1'b0;
                end
                if (alloc_now) begin
                    vld[tail]   <= 1'b1;
                    done[tail]  <= alloc_ecall;
                    pc_q[tail]  <= alloc_pc;
                    insn_q[tail]<= alloc_insn;
                    rd_q[tail]  <= alloc_rd;
                    prd_q[tail] <= alloc_prd;
                    old_q[tail] <= alloc_old_prd;
                    wr_q[tail]  <= alloc_writes;
                    ec_q[tail]  <= alloc_ecall;
                end
                if (alu_complete && vld[alu_tag]) begin
                    done[alu_tag] <= 1'b1;
                end
                if (mem_complete && vld[mem_tag]) begin
                    done[mem_tag] <= 1'b1;
                end
            end
        end
    end

endmodule
