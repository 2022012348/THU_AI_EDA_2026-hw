`timescale 1ns/1ps
module cpu_execution_probe;
    logic clk = 0;
    always #5 clk = ~clk;
    logic rst_n = 0;
    wire [31:0] imem_addr, dmem_addr, exit_code, reg_data, retire_pc, retire_insn;
    wire imem_valid, dmem_valid, dmem_we, halted, retire_valid;
    wire bp_resolve, bp_mispredict, ooo_issue, ooo_bypass;
    logic [31:0] imem_rdata;
    integer mode = 1;
    integer cycle, data_wait = 0;
    integer instruction_wait = 0, instruction_reads = 0, instruction_changes = 0;
    logic slow_if = 0, slow_dmem = 0, imem_waiting = 0, dmem_waiting = 0;
    logic link_recovery = 0;
    logic dmem_held_we;
    logic [31:0] dmem_held_addr, dmem_held_data, pre_edge_imem_addr;
    logic [3:0] dmem_held_be;
    wire [31:0] dmem_wdata;
    wire [3:0] dmem_be;
    integer dmem_changes = 0, halt_changes = 0;
    logic [31:0] held_imem_addr;
    integer retired = 0, resolved = 0, misses = 0, issued = 0, overlaps = 0;
    integer reads = 0, writes = 0;
    logic [31:0] last_retire_pc = 32'hffffffff;
    logic ordered = 1;
    string first_retirements = "";
    string first_reads = "";
    wire dmem_ready = (mode == 3 || mode == 8 || mode == 9 || mode == 10 || slow_dmem) ? (data_wait == 12) : 1'b1;
    wire imem_ready = !slow_if || instruction_wait == 3;
    wire [31:0] dmem_rdata = dmem_addr == 32'h100 ? (mode == 8 ? 32'h0000292a : 32'd41) : 32'd0;

    always @* begin
        imem_rdata = 32'h00000013;
        if (mode == 2) begin
            case (imem_addr)
                32'h00: imem_rdata = 32'h01000093; // addi x1, x0, 16
                32'h04: imem_rdata = 32'hfff08093; // addi x1, x1, -1
                32'h08: imem_rdata = slow_if ? 32'h00000013 : 32'hfe009ee3; // nop or bne x1, x0, -4
                32'h0c: imem_rdata = slow_if ? 32'hfe009ce3 : 32'h00700513; // bne x1, x0, -8 or addi x10, x0, 7
                32'h10: imem_rdata = slow_if ? 32'h00700513 : 32'h00000073;
                32'h14: imem_rdata = slow_if ? 32'h00000073 : 32'h00000013;
                default: imem_rdata = 32'h00000013;
            endcase
        end else if (mode == 4) begin
            case (imem_addr)
                32'h00: imem_rdata = 32'h00100093; // addi x1, x0, 1
                32'h04: imem_rdata = link_recovery ? 32'h01c002ef : 32'h01c0006f; // jal x5/x0, +28
                32'h08: imem_rdata = 32'h06300513; // wrong path: x10=99
                32'h0c: imem_rdata = 32'h00000073; // wrong path: ecall
                32'h20: imem_rdata = link_recovery ? 32'h00328513 : 32'h00700513; // x10=x5+3 or x10=7
                32'h24: imem_rdata = 32'h00000073; // ecall
                default: imem_rdata = 32'h00000013;
            endcase
        end else if (mode == 5) begin
            case (imem_addr)
                32'h00: imem_rdata = 32'h10000093; // addi x1, x0, 0x100
                32'h04: imem_rdata = 32'h0000a503; // lw x10, 0(x1)
                32'h08: imem_rdata = 32'h00000073; // ecall
                32'h0c: imem_rdata = 32'h06300513; // younger x10=99
                32'h10: imem_rdata = 32'h00102023; // younger sw x1, 0(x0)
                default: imem_rdata = 32'h00000013;
            endcase
        end else if (mode == 6) begin
            case (imem_addr)
                32'h00: imem_rdata = 32'h02000093;
                32'h04: imem_rdata = 32'h00000013;
                32'h08: imem_rdata = 32'h00008067;
                32'h0c: imem_rdata = 32'h00102023;
                32'h10: imem_rdata = 32'h00000073;
                32'h20: imem_rdata = 32'h04000093;
                32'h24: imem_rdata = 32'hfe5ff06f;
                32'h40: imem_rdata = 32'h00700513;
                32'h44: imem_rdata = 32'h00000013;
                32'h48: imem_rdata = 32'h00150513;
                32'h4c: imem_rdata = 32'h00000073;
                default: imem_rdata = 32'h00000013;
            endcase
        end else if (mode == 7) begin
            if (imem_addr < 32'h30)
                imem_rdata = 32'h00110113; // addi x2, x2, 1
            else if (imem_addr == 32'h30)
                imem_rdata = 32'h00010513; // addi x10, x2, 0
            else if (imem_addr == 32'h34)
                imem_rdata = 32'h00000073;
        end else if (mode == 8) begin
            case (imem_addr)
                32'h00: imem_rdata = 32'h10000093; // addi x1, x0, 0x100
                32'h04: imem_rdata = 32'h0000a103; // lw x2, 0(x1)
                32'h08: imem_rdata = 32'h0010c183; // lbu x3, 1(x1)
                32'h0c: imem_rdata = 32'h00018513; // addi x10, x3, 0
                32'h10: imem_rdata = 32'h00000073;
                default: imem_rdata = 32'h00000013;
            endcase
        end else if (mode == 9) begin
            case (imem_addr)
                32'h00: imem_rdata = 32'h10000093; // addi x1, x0, 0x100
                32'h04: imem_rdata = 32'h00700113; // addi x2, x0, 7
                32'h08: imem_rdata = 32'h0020a023; // sw x2, 0(x1)
                32'h0c: imem_rdata = 32'h01300193; // addi x3, x0, 19
                32'h10: imem_rdata = 32'h00018513; // addi x10, x3, 0
                32'h14: imem_rdata = 32'h00000073;
                default: imem_rdata = 32'h00000013;
            endcase
        end else if (mode == 10) begin
            if (imem_addr == 32'h00)
                imem_rdata = 32'h10000093; // x1 = 0x100
            else if (imem_addr == 32'h04)
                imem_rdata = 32'h0000a103; // x2 = delayed load of 41
            else if (imem_addr >= 32'h08 && imem_addr <= 32'h34)
                imem_rdata = {imem_addr[13:2] - 12'd1, 20'h00193}; // x3 = 1 through 12
            else if (imem_addr == 32'h38)
                imem_rdata = 32'h00218533; // x10 = x3 + x2
            else if (imem_addr == 32'h3c)
                imem_rdata = 32'h00000073;
        end else begin
            case (imem_addr)
                32'h00: imem_rdata = 32'h10000093; // addi x1, x0, 0x100
                32'h04: imem_rdata = 32'h0000a103; // lw x2, 0(x1)
                32'h08: imem_rdata = mode == 3 ? 32'h00900193 : 32'h00110513; // x3=9 or x10=x2+1
                32'h0c: imem_rdata = mode == 3 ? 32'h00118513 : 32'h00000073; // x10=x3+1 or ecall
                32'h10: imem_rdata = mode == 3 ? 32'h00000073 : 32'h00000013;
                default: imem_rdata = 32'h00000013;
            endcase
        end
    end

    always @(posedge clk) begin
        if (!rst_n || !dmem_valid || dmem_ready)
            data_wait <= 0;
        else
            data_wait <= data_wait + 1;
        if (!rst_n) begin
            dmem_waiting <= 0;
        end else begin
            if (dmem_waiting && (dmem_valid !== 1'b1 || dmem_addr !== dmem_held_addr
                    || dmem_we !== dmem_held_we || (dmem_held_we
                    && (dmem_wdata !== dmem_held_data || dmem_be !== dmem_held_be))))
                dmem_changes <= dmem_changes + 1;
            dmem_waiting <= dmem_valid && !dmem_ready;
            if (dmem_valid && !dmem_ready) begin
                dmem_held_addr <= dmem_addr;
                dmem_held_we <= dmem_we;
                dmem_held_data <= dmem_wdata;
                dmem_held_be <= dmem_be;
            end
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            instruction_wait <= 0;
            imem_waiting <= 0;
        end else begin
            if (imem_waiting && (imem_valid !== 1'b1 || imem_addr !== held_imem_addr))
                instruction_changes <= instruction_changes + 1;
            if (imem_valid && imem_ready)
                instruction_reads <= instruction_reads + 1;
            imem_waiting <= imem_valid && !imem_ready;
            if (imem_valid && !imem_ready) begin
                held_imem_addr <= imem_addr;
                instruction_wait <= instruction_wait + 1;
            end else begin
                instruction_wait <= 0;
            end
        end
    end

    rv32i5 dut (
        .clk(clk), .rst_n(rst_n), .imem_addr(imem_addr), .imem_rdata(imem_rdata),
        .imem_valid(imem_valid), .imem_ready(imem_ready),
        .dmem_valid(dmem_valid), .dmem_we(dmem_we), .dmem_addr(dmem_addr),
        .dmem_rdata(dmem_rdata), .dmem_ready(dmem_ready),
        .dmem_wdata(dmem_wdata), .dmem_be(dmem_be),
        .dbg_reg_addr(mode == 2 ? 5'd1 : mode == 4 ? 5'd10 : mode == 9 ? 5'd3 : 5'd2), .dbg_reg_data(reg_data),
        .halted(halted), .exit_code(exit_code), .dbg_retire_valid(retire_valid),
        .dbg_retire_pc(retire_pc), .dbg_retire_insn(retire_insn),
        .dbg_bp_resolve(bp_resolve), .dbg_bp_mispredict(bp_mispredict),
        .dbg_ooo_issue(ooo_issue), .dbg_ooo_bypass(ooo_bypass)
    );

    initial begin
        if (!$value$plusargs("scenario=%d", mode)) mode = 1;
        slow_if = $test$plusargs("slow_if");
        slow_dmem = $test$plusargs("slow_dmem");
        link_recovery = $test$plusargs("link_recovery");
        repeat (3) @(negedge clk);
        rst_n = 1;
        for (cycle = 0; cycle < 1024 && halted !== 1'b1; cycle = cycle + 1) begin
            @(posedge clk);
            pre_edge_imem_addr = imem_addr;
            if (dmem_valid === 1'b1 && dmem_ready) begin
                if (dmem_we === 1'b1) writes = writes + 1;
                else begin
                    if (reads < 4)
                        first_reads = {first_reads, $sformatf("%h:%h,", dmem_addr, dmem_rdata)};
                    reads = reads + 1;
                end
            end
            #1;
            if (retire_valid === 1'b1) begin
                if (mode == 4) begin
                    case (retired)
                        0: if (retire_pc !== 32'h00 || retire_insn !== 32'h00100093) ordered = 0;
                        1: if (retire_pc !== 32'h04 || retire_insn !== (link_recovery ? 32'h01c002ef : 32'h01c0006f)) ordered = 0;
                        2: if (retire_pc !== 32'h20 || retire_insn !== (link_recovery ? 32'h00328513 : 32'h00700513)) ordered = 0;
                        3: if (retire_pc !== 32'h24 || retire_insn !== 32'h00000073) ordered = 0;
                        default: ordered = 0;
                    endcase
                end else if (mode == 6) begin
                    case (retired)
                        0: if (retire_pc !== 32'h00 || retire_insn !== 32'h02000093) ordered = 0;
                        1: if (retire_pc !== 32'h04 || retire_insn !== 32'h00000013) ordered = 0;
                        2: if (retire_pc !== 32'h08 || retire_insn !== 32'h00008067) ordered = 0;
                        3: if (retire_pc !== 32'h20 || retire_insn !== 32'h04000093) ordered = 0;
                        4: if (retire_pc !== 32'h24 || retire_insn !== 32'hfe5ff06f) ordered = 0;
                        5: if (retire_pc !== 32'h08 || retire_insn !== 32'h00008067) ordered = 0;
                        6: if (retire_pc !== 32'h40 || retire_insn !== 32'h00700513) ordered = 0;
                        7: if (retire_pc !== 32'h44 || retire_insn !== 32'h00000013) ordered = 0;
                        8: if (retire_pc !== 32'h48 || retire_insn !== 32'h00150513) ordered = 0;
                        9: if (retire_pc !== 32'h4c || retire_insn !== 32'h00000073) ordered = 0;
                        default: ordered = 0;
                    endcase
                end else if (mode != 2 && retire_pc !== 32'(4 * retired)) ordered = 0;
                if (retired < 8)
                    first_retirements = {first_retirements, $sformatf("%h:%h,", retire_pc, retire_insn)};
                retired = retired + 1;
                last_retire_pc = retire_pc;
            end
            if (bp_resolve === 1'b1) resolved = resolved + 1;
            if (bp_mispredict === 1'b1) misses = misses + 1;
            if (ooo_issue === 1'b1) issued = issued + 1;
            if (ooo_bypass === 1'b1 && dmem_valid === 1'b1 && !dmem_ready) overlaps = overlaps + 1;
        end
        if (slow_dmem && halted === 1'b1) begin
            repeat (3) begin
                if (imem_addr !== pre_edge_imem_addr || imem_valid !== 1'b0 || dmem_valid !== 1'b0)
                    halt_changes = halt_changes + 1;
                @(posedge clk);
                #1;
            end
        end
        $display("EXEC|scenario=%0d|cycles=%0d|retired=%0d|last_pc=%h|ordered=%b|halted=%b|exit=%h|register=%h|if_reads=%0d|if_changes=%0d|reads=%0d|writes=%0d|resolved=%0d|mispredicts=%0d|issues=%0d|overlaps=%0d|first_retirements=%s|first_reads=%s|dmem_changes=%0d|halt_changes=%0d",
                 mode, cycle, retired, last_retire_pc, ordered, halted, exit_code, reg_data, instruction_reads, instruction_changes,
                 reads, writes, resolved, misses, issued, overlaps, first_retirements, first_reads, dmem_changes, halt_changes);
        $finish;
    end
endmodule
