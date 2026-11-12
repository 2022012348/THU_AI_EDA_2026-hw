module mem_stage (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        mem_hold,
    input  logic        ex_mem_valid,
    input  logic [31:0] ex_mem_pc,
    input  logic [31:0] ex_mem_insn,
    input  logic [31:0] ex_mem_alu_result,
    input  logic [31:0] ex_mem_store_data,
    input  logic [4:0]  ex_mem_rd,
    input  logic        ex_mem_reg_write,
    input  logic        ex_mem_mem_read,
    input  logic        ex_mem_mem_write,
    input  logic [2:0]  ex_mem_funct3,
    input  logic        ex_mem_ecall,
    input  logic [31:0] ex_mem_pred_next_pc,
    input  logic        ex_mem_pred_valid,
    input  logic [1:0]  ex_mem_epoch,
    input  logic [31:0] mem_wb_fwd_data,
    output logic        dcache_cpu_req_valid,
    output logic        dcache_cpu_req_we,
    output logic [31:0] dcache_cpu_req_addr,
    output logic [31:0] dcache_cpu_req_wdata,
    output logic [3:0]  dcache_cpu_req_be,
    input  logic        dcache_cpu_req_ready,
    input  logic        dcache_cpu_resp_valid,
    input  logic [31:0] dcache_cpu_resp_rdata,
    output logic [1:0]  dcache_flush_epoch,
    output logic        mem_busy,
    output logic        mem_wb_valid,
    output logic [31:0] mem_wb_pc,
    output logic [31:0] mem_wb_insn,
    output logic [31:0] mem_wb_alu_result,
    output logic [31:0] mem_wb_load_data,
    output logic [4:0]  mem_wb_rd,
    output logic        mem_wb_reg_write,
    output logic        mem_wb_mem_read,
    output logic        mem_wb_ecall,
    output logic [1:0]  mem_wb_epoch
);

    // transaction state
    logic        txn_active;
    logic        txn_issued;
    logic        txn_is_load;
    logic [31:0] txn_addr;
    logic [2:0]  txn_funct3;
    logic [31:0] txn_pc;
    logic [31:0] txn_insn;
    logic [31:0] txn_alu_result;
    logic [4:0]  txn_rd;
    logic        txn_reg_write;
    logic        txn_ecall;
    logic [1:0]  txn_epoch;

    logic [31:0] store_data_fwd;
    logic [31:0] req_wdata;
    logic [3:0]  req_be;

    assign store_data_fwd = (mem_wb_fwd_data != 32'b0 && ex_mem_rd != 5'd0) ? ex_mem_store_data : ex_mem_store_data;

    // store data / be
    always_comb begin
        case (ex_mem_funct3)
            3'b000: begin // SB
                req_wdata = {4{ex_mem_store_data[7:0]}};
                req_be    = 4'b0001 << ex_mem_alu_result[1:0];
            end
            3'b001: begin // SH
                req_wdata = {2{ex_mem_store_data[15:0]}};
                req_be    = ex_mem_alu_result[1] ? 4'b1100 : 4'b0011;
            end
            default: begin // SW
                req_wdata = ex_mem_store_data;
                req_be    = 4'b1111;
            end
        endcase
    end

    assign dcache_cpu_req_valid = txn_active & ~txn_issued & rst_n;
    assign dcache_cpu_req_we    = ~txn_is_load;
    assign dcache_cpu_req_addr  = txn_addr;
    assign dcache_cpu_req_wdata = req_wdata;
    assign dcache_cpu_req_be    = req_be;
    assign dcache_flush_epoch   = txn_epoch;

    assign mem_busy = txn_active;

    // load extension
    logic [7:0]  load_byte;
    logic [15:0] load_half;
    logic [31:0] load_ext;

    always_comb begin
        load_byte = dcache_cpu_resp_rdata[8*txn_addr[1:0] +: 8];
        load_half = txn_addr[1] ? dcache_cpu_resp_rdata[31:16] : dcache_cpu_resp_rdata[15:0];
        case (txn_funct3)
            3'b000: load_ext = {{24{load_byte[7]}}, load_byte};
            3'b001: load_ext = {{16{load_half[15]}}, load_half};
            3'b010: load_ext = dcache_cpu_resp_rdata;
            3'b100: load_ext = {24'b0, load_byte};
            3'b101: load_ext = {16'b0, load_half};
            default: load_ext = dcache_cpu_resp_rdata;
        endcase
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            txn_active      <= 1'b0;
            txn_issued      <= 1'b0;
            txn_is_load     <= 1'b0;
            txn_addr        <= 32'b0;
            txn_funct3      <= 3'b0;
            txn_pc          <= 32'b0;
            txn_insn        <= 32'b0;
            txn_alu_result  <= 32'b0;
            txn_rd          <= 5'b0;
            txn_reg_write   <= 1'b0;
            txn_ecall       <= 1'b0;
            txn_epoch       <= 2'b0;
            mem_wb_valid    <= 1'b0;
            mem_wb_pc       <= 32'b0;
            mem_wb_insn     <= 32'b0;
            mem_wb_alu_result <= 32'b0;
            mem_wb_load_data  <= 32'b0;
            mem_wb_rd       <= 5'b0;
            mem_wb_reg_write<= 1'b0;
            mem_wb_mem_read <= 1'b0;
            mem_wb_ecall    <= 1'b0;
            mem_wb_epoch    <= 2'b0;
        end else begin
            // default: clear mem_wb valid unless set below
            mem_wb_valid <= 1'b0;

            // start new transaction
            if (!txn_active && ex_mem_valid && (ex_mem_mem_read || ex_mem_mem_write)) begin
                txn_active     <= 1'b1;
                txn_issued     <= 1'b0;
                txn_is_load    <= ex_mem_mem_read;
                txn_addr       <= ex_mem_alu_result;
                txn_funct3     <= ex_mem_funct3;
                txn_pc         <= ex_mem_pc;
                txn_insn       <= ex_mem_insn;
                txn_alu_result <= ex_mem_alu_result;
                txn_rd         <= ex_mem_rd;
                txn_reg_write  <= ex_mem_reg_write;
                txn_ecall      <= ex_mem_ecall;
                txn_epoch      <= ex_mem_epoch;
            end

            // request acceptance
            if (txn_active && !txn_issued && dcache_cpu_req_ready) begin
                txn_issued <= 1'b1;
            end

            // response
            if (txn_active && txn_issued && dcache_cpu_resp_valid) begin
                txn_active <= 1'b0;
                txn_issued <= 1'b0;
                mem_wb_valid      <= 1'b1;
                mem_wb_pc         <= txn_pc;
                mem_wb_insn       <= txn_insn;
                mem_wb_alu_result <= txn_alu_result;
                mem_wb_load_data  <= txn_is_load ? load_ext : 32'b0;
                mem_wb_rd         <= txn_rd;
                mem_wb_reg_write  <= txn_reg_write;
                mem_wb_mem_read   <= txn_is_load;
                mem_wb_ecall      <= txn_ecall;
                mem_wb_epoch      <= txn_epoch;
            end else if (!txn_active && ex_mem_valid && !ex_mem_mem_read && !ex_mem_mem_write) begin
                // non-memory instruction passes through
                mem_wb_valid      <= 1'b1;
                mem_wb_pc         <= ex_mem_pc;
                mem_wb_insn       <= ex_mem_insn;
                mem_wb_alu_result <= ex_mem_alu_result;
                mem_wb_load_data  <= 32'b0;
                mem_wb_rd         <= ex_mem_rd;
                mem_wb_reg_write  <= ex_mem_reg_write;
                mem_wb_mem_read   <= 1'b0;
                mem_wb_ecall      <= ex_mem_ecall;
                mem_wb_epoch      <= ex_mem_epoch;
            end
        end
    end

endmodule
