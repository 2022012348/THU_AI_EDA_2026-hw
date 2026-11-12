module branch_predictor (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        lookup_valid,
    input  logic [31:0] lookup_pc,
    output logic        hit,
    output logic [31:0] target,
    output logic        pred_taken,
    input  logic        train_valid,
    input  logic [31:0] train_pc,
    input  logic [31:0] train_target,
    input  logic        train_taken,
    input  logic        train_is_branch,
    input  logic        train_is_jal,
    input  logic        train_is_jalr
);

    logic        btb_valid [0:15];
    logic [25:0] btb_tag   [0:15];
    logic [31:0] btb_target[0:15];
    logic [1:0]  btb_type  [0:15];
    logic [1:0]  bht       [0:15];

    logic [3:0]  lookup_idx;
    logic [25:0] lookup_tag;
    logic [3:0]  train_idx;
    logic [25:0] train_tag;
    logic        btb_hit;
    logic [1:0]  btb_type_sel;
    logic [1:0]  bht_val;
    logic [1:0]  bht_train_val;
    logic        bht_taken;
    logic        is_ctrl_train;
    logic        need_write;
    logic [1:0]  new_type;
    logic [1:0]  bht_next;

    assign lookup_idx = lookup_pc[5:2];
    assign lookup_tag = lookup_pc[31:6];
    assign train_idx  = train_pc[5:2];
    assign train_tag  = train_pc[31:6];

    assign btb_hit = btb_valid[lookup_idx] && (btb_tag[lookup_idx] == lookup_tag);
    assign btb_type_sel = btb_type[lookup_idx];
    assign bht_val = bht[lookup_idx];
    assign bht_train_val = bht[train_idx];
    assign bht_taken = bht_val[1];

    always_comb begin
        hit        = 1'b0;
        target     = 32'd0;
        pred_taken = 1'b0;
        if (lookup_valid && btb_hit) begin
            hit = 1'b1;
            target = btb_target[lookup_idx];
            if (btb_type_sel == 2'd0)
                pred_taken = bht_taken;
            else
                pred_taken = 1'b1;
        end
    end

    assign is_ctrl_train = train_valid && (train_is_branch || train_is_jal || train_is_jalr);

    always_comb begin
        new_type = 2'd0;
        if (train_is_jal)
            new_type = 2'd1;
        else if (train_is_jalr)
            new_type = 2'd2;
        else
            new_type = 2'd0;
    end

    always_comb begin
        need_write = 1'b0;
        if (is_ctrl_train) begin
            if (train_is_branch) begin
                if (!btb_valid[train_idx] || (btb_tag[train_idx] != train_tag)) begin
                    if (train_taken)
                        need_write = 1'b1;
                end else if ((btb_target[train_idx] != train_target) ||
                             (btb_type[train_idx] != new_type)) begin
                    need_write = 1'b1;
                end
            end else begin
                if (!btb_valid[train_idx] || (btb_tag[train_idx] != train_tag) ||
                    (btb_target[train_idx] != train_target) ||
                    (btb_type[train_idx] != new_type))
                    need_write = 1'b1;
            end
        end
    end

    always_comb begin
        bht_next = bht_train_val;
        if (train_taken) begin
            if (bht_train_val != 2'b11)
                bht_next = bht_train_val + 2'd1;
        end else begin
            if (bht_train_val != 2'b00)
                bht_next = bht_train_val - 2'd1;
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            for (int i = 0; i < 16; i = i + 1) begin
                btb_valid[i]  <= 1'b0;
                btb_tag[i]    <= 26'd0;
                btb_target[i] <= 32'd0;
                btb_type[i]   <= 2'd0;
                bht[i]        <= 2'b01;
            end
        end else if (is_ctrl_train) begin
            if (train_is_branch)
                bht[train_idx] <= bht_next;
            if (need_write) begin
                btb_valid[train_idx]  <= 1'b1;
                btb_tag[train_idx]    <= train_tag;
                btb_target[train_idx] <= train_target;
                btb_type[train_idx]   <= new_type;
            end
        end
    end

endmodule
