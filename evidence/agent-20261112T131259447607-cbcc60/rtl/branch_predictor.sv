module branch_predictor (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] bp_lookup_pc,
    output logic [31:0] bp_lookup_next_pc,
    output logic        bp_lookup_valid,
    input  logic        bp_train_valid,
    input  logic [31:0] bp_train_pc,
    input  logic [31:0] bp_train_actual_next_pc,
    input  logic        bp_train_is_branch,
    input  logic        bp_train_is_jal,
    input  logic        bp_train_is_jalr,
    input  logic        bp_train_taken,
    input  logic [31:0] bp_train_target,
    output logic        bp_resolve,
    output logic        bp_mispredict
);

    // BTB
    logic        btb_valid [0:15];
    logic [25:0] btb_tag   [0:15];
    logic [31:0] btb_target[0:15];
    logic [1:0]  btb_type  [0:15]; // 00 branch, 01 jal, 10 jalr

    // BHT
    logic [1:0]  bht [0:15];

    logic [3:0]  lookup_idx;
    logic [25:0] lookup_tag;
    logic [3:0]  train_idx;
    logic [25:0] train_tag;

    logic        lookup_hit;
    logic [1:0]  lookup_type;
    logic [31:0] lookup_target;
    logic [1:0]  lookup_bht;

    logic [3:0]  train_lookup_idx;
    logic [25:0] train_lookup_tag;
    logic        train_hit;
    logic [1:0]  train_type;
    logic [31:0] train_target;
    logic [1:0]  train_bht;
    logic [31:0] train_pred_next_pc;

    assign lookup_idx  = bp_lookup_pc[5:2];
    assign lookup_tag  = bp_lookup_pc[31:6];
    assign train_idx   = bp_train_pc[5:2];
    assign train_tag   = bp_train_pc[31:6];

    assign lookup_hit    = btb_valid[lookup_idx] && (btb_tag[lookup_idx] == lookup_tag);
    assign lookup_type   = btb_type[lookup_idx];
    assign lookup_target = btb_target[lookup_idx];
    assign lookup_bht    = bht[lookup_idx];

    always_comb begin
        bp_lookup_valid   = 1'b0;
        bp_lookup_next_pc = bp_lookup_pc + 32'd4;
        if (lookup_hit) begin
            if (lookup_type == 2'b00) begin
                if (lookup_bht[1]) begin
                    bp_lookup_valid   = 1'b1;
                    bp_lookup_next_pc = lookup_target;
                end
            end else begin
                bp_lookup_valid   = 1'b1;
                bp_lookup_next_pc = lookup_target;
            end
        end
    end

    assign bp_resolve    = bp_train_valid;

    // Recompute the prediction that was made for the resolving instruction
    // (bp_train_pc) using the pre-update BTB/BHT state, then compare to the
    // actual next PC. Using bp_lookup_next_pc here compares a different
    // instruction's fetch and is always wrong.
    assign train_lookup_idx = bp_train_pc[5:2];
    assign train_lookup_tag = bp_train_pc[31:6];
    assign train_hit        = btb_valid[train_lookup_idx] && (btb_tag[train_lookup_idx] == train_lookup_tag);
    assign train_type       = btb_type[train_lookup_idx];
    assign train_target     = btb_target[train_lookup_idx];
    assign train_bht        = bht[train_lookup_idx];

    always_comb begin
        train_pred_next_pc = bp_train_pc + 32'd4;
        if (train_hit) begin
            if (train_type == 2'b00) begin
                if (train_bht[1])
                    train_pred_next_pc = train_target;
            end else begin
                train_pred_next_pc = train_target;
            end
        end
    end

    assign bp_mispredict = bp_train_valid & (train_pred_next_pc != bp_train_actual_next_pc);

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            for (int i = 0; i < 16; i++) begin
                btb_valid[i]  <= 1'b0;
                btb_tag[i]    <= 26'b0;
                btb_target[i] <= 32'b0;
                btb_type[i]   <= 2'b0;
                bht[i]        <= 2'b01;
            end
        end else if (bp_train_valid) begin
            if (bp_train_is_branch) begin
                // BHT update
                if (bp_train_taken) begin
                    if (bht[train_idx] != 2'b11)
                        bht[train_idx] <= bht[train_idx] + 2'd1;
                end else begin
                    if (bht[train_idx] != 2'b00)
                        bht[train_idx] <= bht[train_idx] - 2'd1;
                end
                // BTB allocate/update
                if (bp_train_taken) begin
                    btb_valid[train_idx]  <= 1'b1;
                    btb_tag[train_idx]    <= train_tag;
                    btb_target[train_idx] <= bp_train_target;
                    btb_type[train_idx]   <= 2'b00;
                end
            end else if (bp_train_is_jal) begin
                btb_valid[train_idx]  <= 1'b1;
                btb_tag[train_idx]    <= train_tag;
                btb_target[train_idx] <= bp_train_target;
                btb_type[train_idx]   <= 2'b01;
            end else if (bp_train_is_jalr) begin
                btb_valid[train_idx]  <= 1'b1;
                btb_tag[train_idx]    <= train_tag;
                btb_target[train_idx] <= bp_train_target;
                btb_type[train_idx]   <= 2'b10;
            end
        end
    end

endmodule
