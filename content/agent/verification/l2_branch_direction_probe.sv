`timescale 1ns/1ps
module l2_branch_direction_probe;
    logic clk = 0;
    always #5 clk = ~clk;
    logic rst_n = 0;
    wire [31:0] imem_addr, exit_code, retire_pc, retire_insn;
    wire imem_valid, dmem_valid, dmem_we, halted, retire_valid, bp_resolve, bp_mispredict;
    logic [31:0] imem_rdata;
    integer wait_cycles = 0;
    wire imem_ready = wait_cycles == 3;
    integer cycle, retired = 0, resolved = 0, misses = 0, writes = 0;
    logic ordered = 1;
    logic [31:0] expected_pc [0:26];

    function automatic [31:0] word_at(input [31:0] addr);
        case (addr)
            32'h00: word_at = 32'h00000093;
            32'h04: word_at = 32'h00000113;
            32'h08: word_at = 32'h00008c63;
            32'h0c: word_at = 32'h00000093;
            32'h10: word_at = 32'hff9ff06f;
            32'h20: word_at = 32'h00110113;
            32'h24: word_at = 32'h00300193;
            32'h28: word_at = 32'h02310063;
            32'h2c: word_at = 32'h00200193;
            32'h30: word_at = 32'h00214093;
            32'h34: word_at = 32'h0010b093;
            32'h38: word_at = 32'hfd1ff06f;
            32'h48: word_at = 32'h00900513;
            32'h4c: word_at = 32'h00000073;
            default: word_at = 32'h00000013;
        endcase
    endfunction
    assign imem_rdata = word_at(imem_addr);

    always @(posedge clk) begin
        if (!rst_n || !imem_valid || imem_ready)
            wait_cycles <= 0;
        else
            wait_cycles <= wait_cycles + 1;
    end

    rv32i5 dut (
        .clk(clk), .rst_n(rst_n), .imem_addr(imem_addr), .imem_rdata(imem_rdata),
        .imem_valid(imem_valid), .imem_ready(imem_ready),
        .dmem_valid(dmem_valid), .dmem_we(dmem_we), .dmem_rdata(32'b0), .dmem_ready(1'b1),
        .dbg_reg_addr(5'b0), .halted(halted), .exit_code(exit_code),
        .dbg_retire_valid(retire_valid), .dbg_retire_pc(retire_pc), .dbg_retire_insn(retire_insn),
        .dbg_bp_resolve(bp_resolve), .dbg_bp_mispredict(bp_mispredict)
    );

    initial begin
        expected_pc[0] = 32'h00;
        expected_pc[1] = 32'h04;
        expected_pc[2] = 32'h08;
        expected_pc[3] = 32'h20;
        expected_pc[4] = 32'h24;
        expected_pc[5] = 32'h28;
        expected_pc[6] = 32'h2c;
        expected_pc[7] = 32'h30;
        expected_pc[8] = 32'h34;
        expected_pc[9] = 32'h38;
        expected_pc[10] = 32'h08;
        expected_pc[11] = 32'h20;
        expected_pc[12] = 32'h24;
        expected_pc[13] = 32'h28;
        expected_pc[14] = 32'h2c;
        expected_pc[15] = 32'h30;
        expected_pc[16] = 32'h34;
        expected_pc[17] = 32'h38;
        expected_pc[18] = 32'h08;
        expected_pc[19] = 32'h0c;
        expected_pc[20] = 32'h10;
        expected_pc[21] = 32'h08;
        expected_pc[22] = 32'h20;
        expected_pc[23] = 32'h24;
        expected_pc[24] = 32'h28;
        expected_pc[25] = 32'h48;
        expected_pc[26] = 32'h4c;
        repeat (3) @(negedge clk);
        rst_n = 1;
        for (cycle = 0; cycle < 1024 && halted !== 1'b1; cycle = cycle + 1) begin
            @(posedge clk);
            if (dmem_valid === 1'b1 && dmem_we === 1'b1)
                writes = writes + 1;
            #1;
            if (retire_valid === 1'b1) begin
                if (retired >= 27 || retire_pc !== expected_pc[retired] || retire_insn !== word_at(retire_pc))
                    ordered = 0;
                retired = retired + 1;
            end
            if (bp_resolve === 1'b1) resolved = resolved + 1;
            if (bp_mispredict === 1'b1) misses = misses + 1;
        end
        $display("DIRECTION|retired=%0d|ordered=%b|halted=%b|exit=%h|writes=%0d|resolved=%0d|mispredicts=%0d",
                 retired, ordered, halted, exit_code, writes, resolved, misses);
        $finish;
    end
endmodule
