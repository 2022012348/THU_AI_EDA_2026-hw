`timescale 1ns/1ps
module cpu_retirement_probe;
    logic clk = 0;
    always #5 clk = ~clk;

    logic rst_n = 0;
    wire [31:0] imem_addr, dmem_addr, retire_pc, retire_insn, exit_code, reg_data;
    wire imem_valid, dmem_valid, halted, retire_valid;
    logic [31:0] imem_rdata;
    integer reads = 0, retires = 0, ecalls = 0, data_requests = 0;
    integer cycle;
    logic [31:0] first_retire_pc = 32'hffffffff;
    logic [31:0] first_retire_insn = 32'hffffffff;

    // A generic reset / ALU / ECALL smoke program under a zero-wait word bus.
    always @* begin
        case (imem_addr)
            32'h0: imem_rdata = 32'h00700513;  // addi x10, x0, 7
            32'h4: imem_rdata = 32'h00000073;  // ecall
            default: imem_rdata = 32'h00000013;
        endcase
    end

    rv32i5 dut (
        .clk(clk), .rst_n(rst_n),
        .imem_addr(imem_addr), .imem_rdata(imem_rdata),
        .imem_valid(imem_valid), .imem_ready(1'b1),
        .dmem_valid(dmem_valid), .dmem_addr(dmem_addr), .dmem_rdata(32'b0), .dmem_ready(1'b1),
        .dbg_reg_addr(5'd10), .dbg_reg_data(reg_data),
        .halted(halted), .exit_code(exit_code), .dbg_retire_valid(retire_valid),
        .dbg_retire_pc(retire_pc), .dbg_retire_insn(retire_insn)
    );

    initial begin
        #1;
        $display("RESET|imem_valid=%b|dmem_valid=%b|imem_addr=%h", imem_valid, dmem_valid, imem_addr);
        repeat (3) @(negedge clk);
        rst_n = 1;
        for (cycle = 0; cycle < 256 && halted !== 1'b1; cycle = cycle + 1) begin
            @(posedge clk);
            if (imem_valid === 1'b1) reads = reads + 1;
            if (dmem_valid === 1'b1) data_requests = data_requests + 1;
            #1;
            if (retire_valid === 1'b1) begin
                if (retires == 0) begin
                    first_retire_pc = retire_pc;
                    first_retire_insn = retire_insn;
                end
                retires = retires + 1;
                if (retire_insn == 32'h00000073) ecalls = ecalls + 1;
            end
        end
        $display("RETIREMENT|cycles=%0d|imem_accepts=%0d|dmem_accepts=%0d|retired=%0d|ecalls=%0d|first_pc=%h|first_insn=%h|last_pc=%h|last_insn=%h|halted=%b|exit=%h|x10=%h|final_imem_valid=%b|final_imem_addr=%h",
                 cycle, reads, data_requests, retires, ecalls, first_retire_pc, first_retire_insn,
                 retire_pc, retire_insn, halted, exit_code, reg_data, imem_valid, imem_addr);
        $finish;
    end
endmodule
