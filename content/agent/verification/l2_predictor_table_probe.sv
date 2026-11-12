`timescale 1ns/1ps
module l2_predictor_table_probe;
    logic clk = 0;
    always #5 clk = ~clk;
    logic rst_n = 0;
    logic lookup_valid = 1;
    logic [31:0] lookup_pc = 32'h18;
    wire hit, pred_taken;
    wire [31:0] target;
    logic train_valid = 0;
    logic [31:0] train_pc = 32'h08;
    logic [31:0] train_target = 32'h20;
    logic train_taken = 0;
    logic train_is_branch = 1, train_is_jal = 0, train_is_jalr = 0;

    branch_predictor dut (
        .clk(clk), .rst_n(rst_n),
        .lookup_valid(lookup_valid), .lookup_pc(lookup_pc),
        .hit(hit), .target(target), .pred_taken(pred_taken),
        .train_valid(train_valid), .train_pc(train_pc), .train_target(train_target),
        .train_taken(train_taken), .train_is_branch(train_is_branch),
        .train_is_jal(train_is_jal), .train_is_jalr(train_is_jalr)
    );

    task automatic train_branch(input logic taken);
        @(negedge clk);
        // Train PC 0x08 while the IF lookup uses a different BHT entry.
        lookup_pc = 32'h18;
        train_taken = taken;
        train_valid = 1;
        @(negedge clk);
        train_valid = 0;
    endtask

    initial begin
        repeat (2) @(negedge clk);
        rst_n = 1;
        train_branch(1);
        train_branch(1);
        train_branch(0);
        lookup_pc = 32'h08;
        #1;
        $display("PREDICTOR|case=training_index|hit=%b|taken=%b|target=%h|match=%b",
                 hit, pred_taken, target, hit === 1'b1 && pred_taken === 1'b1 && target === 32'h20);
        train_branch(0);
        lookup_pc = 32'h08;
        #1;
        $display("PREDICTOR|case=direction_change|hit=%b|taken=%b|target=%h|match=%b",
                 hit, pred_taken, target, hit === 1'b1 && pred_taken === 1'b0 && target === 32'h20);
        lookup_pc = 32'h48;
        #1;
        $display("PREDICTOR|case=tag_alias|hit=%b|taken=%b|target=%h|match=%b",
                 hit, pred_taken, target, hit === 1'b0 && pred_taken === 1'b0);
        $finish;
    end
endmodule
