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
    logic [29:0] btb_tag   [0:15];
    logic [31:0] btb_target[0:15];
    logic [1:0]  btb_type  [0:15];
    logic [1:0]  bht       [0:15];

    logic [3:0]  lookup_idx;
    logic [29:0] lookup_tag;
    logic [3:0]  train_idx;
    logic [29:0] train_tag;
    logic        btb_hit;
    logic [1:0]  btb_kind;
    logic [1:0]  bht_cnt;
    logic        dir_taken;
    logic [1:0]  bht_next;

    assign lookup_idx = lookup_pc[5:2];
    assign lookup_tag = lookup_pc[31:2];
    assign train_idx  = train_pc[5:2];
    assign train_tag  = train_pc[31:2];

    assign btb_hit   = btb_valid[lookup_idx] && (btb_tag[lookup_idx] == lookup_tag);
    assign btb_kind  = btb_type[lookup_idx];
    assign bht_cnt   = bht[lookup_idx];
    assign dir_taken = (bht_cnt == 2'b10) || (bht_cnt == 2'b11);

    assign hit = lookup_valid && btb_hit;
    assign target = btb_target[lookup_idx];
    assign pred_taken = lookup_valid && btb_hit &&
                        ((btb_kind == 2'b00) ? dir_taken : 1'b1);

    always_comb begin
        if (train_taken) begin
            if (bht_cnt == 2'b00)      bht_next = 2'b01;
            else if (bht_cnt == 2'b01) bht_next = 2'b10;
            else if (bht_cnt == 2'b10) bht_next = 2'b11;
            else                       bht_next = 2'b11;
        end else begin
            if (bht_cnt == 2'b00)      bht_next = 2'b00;
            else if (bht_cnt == 2'b01) bht_next = 2'b00;
            else if (bht_cnt == 2'b10) bht_next = 2'b01;
            else                       bht_next = 2'b10;
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            for (int i = 0; i < 16; i = i + 1) begin
                btb_valid[i]  <= 1'b0;
                btb_tag[i]    <= 30'd0;
                btb_target[i] <= 32'd0;
                btb_type[i]   <= 2'b00;
                bht[i]        <= 2'b01;
            end
        end else if (train_valid) begin
            btb_valid[train_idx]  <= 1'b1;
            btb_tag[train_idx]    <= train_tag;
            btb_target[train_idx] <= train_target;
            if (train_is_jalr)      btb_type[train_idx] <= 2'b10;
            else if (train_is_jal)  btb_type[train_idx] <= 2'b01;
            else                    btb_type[train_idx] <= 2'b00;
            if (train_is_branch) begin
                bht[train_idx] <= bht_next;
            end
        end
    end

endmodule
