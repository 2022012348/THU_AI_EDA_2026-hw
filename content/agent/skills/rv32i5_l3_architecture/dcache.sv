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
    input  logic        mem_req_ready,
    output logic [31:0] mem_req_addr,
    output logic        mem_req_we,
    output logic [31:0] mem_req_wdata,
    output logic [3:0]  mem_req_be,
    input  logic        mem_resp_valid,
    input  logic [31:0] mem_resp_data
);

    logic        valid_arr [0:15];
    logic [23:0] tag_arr   [0:15];
    logic [31:0] data_arr  [0:15][0:3];

    logic        busy_q;
    logic        is_store_q;
    logic [1:0]  refill_idx_q;
    logic [31:0] refill_base_q;
    logic [3:0]  refill_line_q;
    logic [23:0] refill_tag_q;
    logic [31:0] refill_word_q [0:3];
    logic [1:0]  refill_epoch_q;
    logic [31:0] req_addr_q;
    logic [31:0] req_wdata_q;
    logic [3:0]  req_be_q;
    logic [1:0]  req_word_q;
    logic [1:0]  req_epoch_q;
    logic        wt_pending_q;
    logic        resp_valid_q;
    logic [31:0] resp_data_q;

    logic [3:0]  req_index;
    logic [23:0] req_tag;
    logic [1:0]  req_word;
    logic        hit;
    logic        req_accept;
    logic        mem_accept;
    logic        refill_done;
    logic [31:0] refill_addr;
    logic [31:0] merged_word;
    logic [31:0] wt_addr;
    logic [31:0] wt_wdata;
    logic [3:0]  wt_be;

    assign req_index = cpu_req_addr[7:4];
    assign req_tag   = cpu_req_addr[31:8];
    assign req_word  = cpu_req_addr[3:2];

    assign hit = valid_arr[req_index] && (tag_arr[req_index] == req_tag);

    assign cpu_req_ready = !busy_q && !wt_pending_q && !resp_valid_q;

    assign req_accept = cpu_req_valid && cpu_req_ready;

    assign mem_req_valid = busy_q || wt_pending_q;
    assign refill_addr   = refill_base_q + {refill_idx_q, 2'b00};
    assign mem_req_addr  = busy_q ? refill_addr : wt_addr;
    assign mem_req_we    = busy_q ? 1'b0 : 1'b1;
    assign mem_req_wdata = busy_q ? 32'd0 : wt_wdata;
    assign mem_req_be    = busy_q ? 4'd0 : wt_be;

    assign mem_accept = mem_req_valid && mem_req_ready;

    assign refill_done = mem_accept && busy_q && (refill_idx_q == 2'd3);

    assign cpu_resp_valid = resp_valid_q;
    assign cpu_resp_rdata = resp_data_q;

    assign wt_addr  = req_addr_q;
    assign wt_wdata = req_wdata_q;
    assign wt_be    = req_be_q;

    always_comb begin
        merged_word = refill_word_q[req_word_q];
        case (req_word_q)
            2'd0: merged_word = refill_word_q[0];
            2'd1: merged_word = refill_word_q[1];
            2'd2: merged_word = refill_word_q[2];
            default: merged_word = refill_word_q[3];
        endcase
        if (req_be_q[0]) merged_word[7:0]   = req_wdata_q[7:0];
        if (req_be_q[1]) merged_word[15:8]  = req_wdata_q[15:8];
        if (req_be_q[2]) merged_word[23:16] = req_wdata_q[23:16];
        if (req_be_q[3]) merged_word[31:24] = req_wdata_q[31:24];
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            for (int i = 0; i < 16; i = i + 1)
                valid_arr[i] <= 1'b0;
            busy_q        <= 1'b0;
            is_store_q    <= 1'b0;
            refill_idx_q  <= 2'd0;
            refill_base_q <= 32'd0;
            refill_line_q <= 4'd0;
            refill_tag_q  <= 24'd0;
            refill_epoch_q<= 2'd0;
            req_addr_q    <= 32'd0;
            req_wdata_q   <= 32'd0;
            req_be_q      <= 4'd0;
            req_word_q    <= 2'd0;
            req_epoch_q   <= 2'd0;
            wt_pending_q  <= 1'b0;
            resp_valid_q  <= 1'b0;
            resp_data_q   <= 32'd0;
            for (int j = 0; j < 4; j = j + 1)
                refill_word_q[j] <= 32'd0;
        end else begin
            resp_valid_q <= 1'b0;

            if (flush) begin
                busy_q <= 1'b0;
            end

            if (req_accept) begin
                req_addr_q  <= cpu_req_addr;
                req_wdata_q <= cpu_req_wdata;
                req_be_q    <= cpu_req_be;
                req_word_q  <= req_word;
                req_epoch_q <= flush_epoch;
                is_store_q  <= cpu_req_we;

                if (hit) begin
                    if (cpu_req_we) begin
                        if (cpu_req_be[0]) data_arr[req_index][req_word][7:0]   <= cpu_req_wdata[7:0];
                        if (cpu_req_be[1]) data_arr[req_index][req_word][15:8]  <= cpu_req_wdata[15:8];
                        if (cpu_req_be[2]) data_arr[req_index][req_word][23:16] <= cpu_req_wdata[23:16];
                        if (cpu_req_be[3]) data_arr[req_index][req_word][31:24] <= cpu_req_wdata[31:24];
                        wt_pending_q <= 1'b1;
                    end else begin
                        resp_valid_q <= 1'b1;
                        resp_data_q  <= data_arr[req_index][req_word];
                    end
                end else begin
                    busy_q        <= 1'b1;
                    refill_idx_q  <= 2'd0;
                    refill_base_q <= {cpu_req_addr[31:4], 4'b0000};
                    refill_line_q <= req_index;
                    refill_tag_q  <= req_tag;
                    refill_epoch_q<= flush_epoch;
                end
            end

            if (mem_accept && busy_q) begin
                refill_word_q[refill_idx_q] <= mem_resp_data;
                if (refill_done) begin
                    busy_q <= 1'b0;
                    if (refill_epoch_q == flush_epoch) begin
                        valid_arr[refill_line_q] <= 1'b1;
                        tag_arr[refill_line_q]   <= refill_tag_q;
                        data_arr[refill_line_q][0] <= refill_word_q[0];
                        data_arr[refill_line_q][1] <= refill_word_q[1];
                        data_arr[refill_line_q][2] <= refill_word_q[2];
                        data_arr[refill_line_q][3] <= mem_resp_data;
                        if (is_store_q) begin
                            if (req_be_q[0]) data_arr[refill_line_q][req_word_q][7:0]   <= req_wdata_q[7:0];
                            if (req_be_q[1]) data_arr[refill_line_q][req_word_q][15:8]  <= req_wdata_q[15:8];
                            if (req_be_q[2]) data_arr[refill_line_q][req_word_q][23:16] <= req_wdata_q[23:16];
                            if (req_be_q[3]) data_arr[refill_line_q][req_word_q][31:24] <= req_wdata_q[31:24];
                            wt_pending_q <= 1'b1;
                        end else begin
                            case (req_word_q)
                                2'd0: resp_data_q <= refill_word_q[0];
                                2'd1: resp_data_q <= refill_word_q[1];
                                2'd2: resp_data_q <= refill_word_q[2];
                                default: resp_data_q <= mem_resp_data;
                            endcase
                            resp_valid_q <= 1'b1;
                        end
                    end
                end else begin
                    refill_idx_q <= refill_idx_q + 2'd1;
                end
            end

            if (mem_accept && wt_pending_q) begin
                wt_pending_q <= 1'b0;
                resp_valid_q <= 1'b1;
                resp_data_q  <= 32'd0;
            end
        end
    end

endmodule
