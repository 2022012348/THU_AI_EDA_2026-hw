module mem_stage (
    input  logic        clk,
    input  logic        rst_n,
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
    input  logic        mem_hold,
    input  logic [31:0] mem_wb_fwd_data,
    input  logic [31:0] dcache_resp_rdata,
    input  logic        dcache_resp_valid,
    output logic        dcache_req_valid,
    output logic        dcache_req_we,
    output logic [31:0] dcache_req_addr,
    output logic [31:0] dcache_req_wdata,
    output logic [3:0]  dcache_req_be,
    input  logic        dcache_req_ready,
    output logic        mem_busy,
    output logic        mem_wb_valid,
    output logic [31:0] mem_wb_pc,
    output logic [31:0] mem_wb_insn,
    output logic [31:0] mem_wb_data,
    output logic [4:0]  mem_wb_rd,
    output logic        mem_wb_reg_write,
    output logic        mem_wb_ecall,
    output logic        mem_wb_is_store,
    output logic        mem_wb_store_done,
    output logic [1:0]  mem_flush_epoch,
    output logic        mem_flush
);

    localparam logic [1:0] M_IDLE = 2'd0;
    localparam logic [1:0] M_REQ  = 2'd1;
    localparam logic [1:0] M_WAIT = 2'd2;

    logic [1:0]  mstate;
    logic [31:0] req_addr_r;
    logic [31:0] req_wdata_r;
    logic [3:0]  req_be_r;
    logic        req_we_r;
    logic [2:0]  req_funct3_r;
    logic [31:0] req_pc_r;
    logic [31:0] req_insn_r;
    logic [4:0]  req_rd_r;
    logic        req_reg_write_r;
    logic        req_ecall_r;

    logic        is_mem_op;
    logic        is_store;
    logic        is_load;
    logic [31:0] store_data_fwd;
    logic [31:0] load_data;
    logic [31:0] wb_data_sel;

    assign is_mem_op = ex_mem_valid && (ex_mem_mem_read || ex_mem_mem_write);
    assign is_store  = ex_mem_mem_write;
    assign is_load   = ex_mem_mem_read;

    assign store_data_fwd = (mem_wb_valid && mem_wb_reg_write && (mem_wb_rd != 5'd0) &&
                             (mem_wb_rd == ex_mem_insn[24:20])) ? mem_wb_fwd_data : ex_mem_store_data;

    // Store byte enables and replicated data
    logic [31:0] st_wdata;
    logic [3:0]  st_be;
    always_comb begin
        st_wdata = ex_mem_store_data;
        st_be    = 4'b1111;
        if (is_store) begin
            case (ex_mem_funct3)
                3'b000: begin // SB
                    st_wdata = {4{store_data_fwd[7:0]}};
                    st_be    = 4'b0001 << ex_mem_alu_result[1:0];
                end
                3'b001: begin // SH
                    st_wdata = {2{store_data_fwd[15:0]}};
                    st_be    = ex_mem_alu_result[1] ? 4'b1100 : 4'b0011;
                end
                default: begin // SW
                    st_wdata = store_data_fwd;
                    st_be    = 4'b1111;
                end
            endcase
        end
    end

    assign dcache_req_valid = (mstate == M_REQ);
    assign dcache_req_we    = req_we_r;
    assign dcache_req_addr  = req_addr_r;
    assign dcache_req_wdata = req_wdata_r;
    assign dcache_req_be    = req_be_r;

    assign mem_busy = (mstate != M_IDLE);

    assign mem_flush_epoch = 2'b0;
    assign mem_flush       = 1'b0;

    // Load data select/extend
    logic [7:0]  sel_byte;
    logic [15:0] sel_half;
    always_comb begin
        sel_byte = dcache_resp_rdata[8*req_addr_r[1:0] +: 8];
        sel_half = req_addr_r[1] ? dcache_resp_rdata[31:16] : dcache_resp_rdata[15:0];
        case (req_funct3_r)
            3'b000: load_data = {{24{sel_byte[7]}}, sel_byte};       // LB
            3'b001: load_data = {{16{sel_half[15]}}, sel_half};      // LH
            3'b010: load_data = dcache_resp_rdata;                   // LW
            3'b100: load_data = {24'b0, sel_byte};                   // LBU
            3'b101: load_data = {16'b0, sel_half};                   // LHU
            default: load_data = dcache_resp_rdata;
        endcase
    end

    always_comb begin
        if (req_funct3_r == 3'b000 || req_funct3_r == 3'b001 || req_funct3_r == 3'b010 ||
            req_funct3_r == 3'b100 || req_funct3_r == 3'b101) begin
            wb_data_sel = load_data;
        end else begin
            wb_data_sel = req_addr_r;
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            mstate           <= M_IDLE;
            req_addr_r       <= 32'b0;
            req_wdata_r      <= 32'b0;
            req_be_r         <= 4'b0;
            req_we_r         <= 1'b0;
            req_funct3_r     <= 3'b0;
            req_pc_r         <= 32'b0;
            req_insn_r       <= 32'b0;
            req_rd_r         <= 5'b0;
            req_reg_write_r  <= 1'b0;
            req_ecall_r      <= 1'b0;
            mem_wb_valid     <= 1'b0;
            mem_wb_pc        <= 32'b0;
            mem_wb_insn      <= 32'b0;
            mem_wb_data      <= 32'b0;
            mem_wb_rd        <= 5'b0;
            mem_wb_reg_write <= 1'b0;
            mem_wb_ecall     <= 1'b0;
            mem_wb_is_store  <= 1'b0;
            mem_wb_store_done<= 1'b0;
        end else begin
            // Clear MEM/WB valid by default unless a new payload is written
            mem_wb_valid <= 1'b0;

            case (mstate)
                M_IDLE: begin
                    if (ex_mem_valid && !mem_hold) begin
                        if (is_mem_op) begin
                            req_addr_r      <= ex_mem_alu_result;
                            req_wdata_r     <= st_wdata;
                            req_be_r        <= st_be;
                            req_we_r        <= is_store;
                            req_funct3_r    <= ex_mem_funct3;
                            req_pc_r        <= ex_mem_pc;
                            req_insn_r      <= ex_mem_insn;
                            req_rd_r        <= ex_mem_rd;
                            req_reg_write_r <= ex_mem_reg_write;
                            req_ecall_r     <= ex_mem_ecall;
                            mstate          <= M_REQ;
                        end else begin
                            mem_wb_valid     <= 1'b1;
                            mem_wb_pc        <= ex_mem_pc;
                            mem_wb_insn      <= ex_mem_insn;
                            mem_wb_data      <= ex_mem_alu_result;
                            mem_wb_rd        <= ex_mem_rd;
                            mem_wb_reg_write <= ex_mem_reg_write;
                            mem_wb_ecall     <= ex_mem_ecall;
                            mem_wb_is_store  <= 1'b0;
                            mem_wb_store_done<= 1'b0;
                        end
                    end
                end
                M_REQ: begin
                    if (dcache_req_ready) begin
                        mstate <= M_WAIT;
                    end
                end
                M_WAIT: begin
                    if (dcache_resp_valid) begin
                        mem_wb_valid     <= 1'b1;
                        mem_wb_pc        <= req_pc_r;
                        mem_wb_insn      <= req_insn_r;
                        mem_wb_data      <= wb_data_sel;
                        mem_wb_rd        <= req_rd_r;
                        mem_wb_reg_write <= req_reg_write_r;
                        mem_wb_ecall     <= req_ecall_r;
                        mem_wb_is_store  <= req_we_r;
                        mem_wb_store_done<= req_we_r;
                        mstate           <= M_IDLE;
                    end
                end
                default: mstate <= M_IDLE;
            endcase
        end
    end

endmodule
