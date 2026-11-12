module dcache (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        cpu_req_valid,
    input  logic        cpu_req_we,
    input  logic [31:0] cpu_req_addr,
    input  logic [31:0] cpu_req_wdata,
    input  logic [3:0]  cpu_req_be,
    output logic        cpu_req_ready,
    output logic        cpu_resp_valid,
    output logic [31:0] cpu_resp_rdata,
    input  logic        flush,
    input  logic [1:0]  flush_epoch,
    output logic        mem_req_valid,
    output logic        mem_req_we,
    output logic [31:0] mem_req_addr,
    output logic [31:0] mem_req_wdata,
    output logic [3:0]  mem_req_be,
    input  logic        mem_req_ready,
    input  logic        mem_resp_valid,
    input  logic [31:0] mem_resp_data
);

    localparam logic [2:0] D_IDLE   = 3'd0;
    localparam logic [2:0] D_REFILL = 3'd1;
    localparam logic [2:0] D_WT     = 3'd2;
    localparam logic [2:0] D_RESP   = 3'd3;

    logic [31:0] data_arr [0:15][0:3];
    logic [15:0] valid_arr;
    logic [23:0] tag_arr [0:15];

    logic [2:0]  dstate;
    logic [3:0]  refill_idx;
    logic [31:0] refill_base;
    logic [3:0]  refill_line;
    logic [31:0] refill_words [0:3];

    logic [31:0] req_addr_r;
    logic [31:0] req_wdata_r;
    logic [3:0]  req_be_r;
    logic        req_we_r;
    logic [31:0] resp_data_r;
    logic        resp_valid_r;

    logic [3:0]  req_idx;
    logic [23:0] req_tag;
    logic [1:0]  req_word;
    logic        hit;

    assign req_idx  = cpu_req_addr[7:4];
    assign req_tag  = cpu_req_addr[31:8];
    assign req_word = cpu_req_addr[3:2];

    assign hit = valid_arr[req_idx] && (tag_arr[req_idx] == req_tag);

    assign cpu_req_ready  = (dstate == D_IDLE) && !resp_valid_r;
    assign cpu_resp_valid = resp_valid_r;
    assign cpu_resp_rdata = resp_data_r;

    assign mem_req_valid = (dstate == D_REFILL) || (dstate == D_WT);
    assign mem_req_we    = (dstate == D_WT);
    assign mem_req_addr  = (dstate == D_REFILL) ? (refill_base + {refill_idx, 2'b00}) : req_addr_r;
    assign mem_req_wdata = req_wdata_r;
    assign mem_req_be    = (dstate == D_WT) ? req_be_r : 4'b1111;

    logic        req_fire;
    logic        mem_fire;
    logic        refill_done;

    assign req_fire    = cpu_req_valid && cpu_req_ready;
    assign mem_fire    = mem_req_valid && mem_req_ready;
    assign refill_done = mem_fire && (refill_idx == 4'd3);

    logic [31:0] merged_word;
    logic [31:0] base_word;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            dstate       <= D_IDLE;
            valid_arr    <= 16'b0;
            refill_idx   <= 4'b0;
            refill_base  <= 32'b0;
            refill_line  <= 4'b0;
            req_addr_r   <= 32'b0;
            req_wdata_r  <= 32'b0;
            req_be_r     <= 4'b0;
            req_we_r     <= 1'b0;
            resp_data_r  <= 32'b0;
            resp_valid_r <= 1'b0;
        end else begin
            resp_valid_r <= 1'b0;

            case (dstate)
                D_IDLE: begin
                    if (req_fire) begin
                        req_addr_r  <= cpu_req_addr;
                        req_wdata_r <= cpu_req_wdata;
                        req_be_r    <= cpu_req_be;
                        req_we_r    <= cpu_req_we;
                        if (hit) begin
                            if (cpu_req_we) begin
                                // update cache lanes then write-through
                                base_word = data_arr[req_idx][req_word];
                                merged_word = base_word;
                                if (cpu_req_be[0]) merged_word[7:0]   = cpu_req_wdata[7:0];
                                if (cpu_req_be[1]) merged_word[15:8]  = cpu_req_wdata[15:8];
                                if (cpu_req_be[2]) merged_word[23:16] = cpu_req_wdata[23:16];
                                if (cpu_req_be[3]) merged_word[31:24] = cpu_req_wdata[31:24];
                                data_arr[req_idx][req_word] <= merged_word;
                                dstate <= D_WT;
                            end else begin
                                resp_data_r  <= data_arr[req_idx][req_word];
                                resp_valid_r <= 1'b1;
                            end
                        end else begin
                            refill_base <= {cpu_req_addr[31:4], 4'b0000};
                            refill_line <= req_idx;
                            refill_idx  <= 4'b0;
                            dstate      <= D_REFILL;
                        end
                    end
                end
                D_REFILL: begin
                    if (mem_fire) begin
                        refill_words[refill_idx] <= mem_resp_data;
                        if (refill_idx == 4'd3) begin
                            data_arr[refill_line][0] <= refill_words[0];
                            data_arr[refill_line][1] <= refill_words[1];
                            data_arr[refill_line][2] <= refill_words[2];
                            data_arr[refill_line][3] <= mem_resp_data;
                            tag_arr[refill_line]     <= refill_base[31:8];
                            valid_arr[refill_line]   <= 1'b1;
                            if (req_we_r) begin
                                // merge store into refilled word
                                base_word = (req_addr_r[3:2] == 2'd0) ? refill_words[0] :
                                            (req_addr_r[3:2] == 2'd1) ? refill_words[1] :
                                            (req_addr_r[3:2] == 2'd2) ? refill_words[2] :
                                            mem_resp_data;
                                merged_word = base_word;
                                if (req_be_r[0]) merged_word[7:0]   = req_wdata_r[7:0];
                                if (req_be_r[1]) merged_word[15:8]  = req_wdata_r[15:8];
                                if (req_be_r[2]) merged_word[23:16] = req_wdata_r[23:16];
                                if (req_be_r[3]) merged_word[31:24] = req_wdata_r[31:24];
                                data_arr[refill_line][req_addr_r[3:2]] <= merged_word;
                                dstate <= D_WT;
                            end else begin
                                resp_data_r  <= (req_addr_r[3:2] == 2'd0) ? refill_words[0] :
                                                (req_addr_r[3:2] == 2'd1) ? refill_words[1] :
                                                (req_addr_r[3:2] == 2'd2) ? refill_words[2] :
                                                mem_resp_data;
                                resp_valid_r <= 1'b1;
                                dstate       <= D_IDLE;
                            end
                        end else begin
                            refill_idx <= refill_idx + 4'd1;
                        end
                    end
                end
                D_WT: begin
                    if (mem_fire) begin
                        resp_data_r  <= 32'b0;
                        resp_valid_r <= 1'b1;
                        dstate       <= D_IDLE;
                    end
                end
                default: dstate <= D_IDLE;
            endcase
        end
    end

endmodule
