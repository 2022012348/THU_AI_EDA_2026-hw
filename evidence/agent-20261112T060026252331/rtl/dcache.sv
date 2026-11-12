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

    logic        line_valid [0:15];
    logic [23:0] line_tag   [0:15];
    logic [31:0] line_words [0:15][0:3];

    logic        busy;
    logic        op_we;
    logic [3:0]  op_idx;
    logic [23:0] op_tag;
    logic [1:0]  op_off;
    logic [31:0] op_addr;
    logic [31:0] op_wdata;
    logic [3:0]  op_be;
    logic [1:0]  op_epoch;

    logic        refilling;
    logic [1:0]  ref_beat;
    logic [31:0] ref_word0;
    logic [31:0] ref_word1;
    logic [31:0] ref_word2;

    logic        wt_pending;
    logic [31:0] wt_addr;
    logic [31:0] wt_wdata;
    logic [3:0]  wt_be;

    logic        resp_valid_r;
    logic [31:0] resp_data_r;

    logic [3:0]  req_index;
    logic [23:0] req_tag;
    logic [1:0]  req_off;
    logic        hit;
    logic        accept;
    logic        beat_accept;
    logic        wt_accept;
    logic [31:0] merged_word;
    logic [31:0] hit_word;
    logic [31:0] load_word;

    assign req_index = cpu_req_addr[7:4];
    assign req_tag   = cpu_req_addr[31:8];
    assign req_off   = cpu_req_addr[3:2];
    assign hit       = line_valid[req_index] && (line_tag[req_index] == req_tag);

    assign busy          = refilling || wt_pending;
    assign cpu_req_ready = rst_n && !busy && !resp_valid_r;
    assign accept        = cpu_req_valid && cpu_req_ready;

    assign mem_req_valid = rst_n && (refilling || wt_pending);
    assign mem_req_we    = wt_pending;
    assign mem_req_addr  = wt_pending ? wt_addr : {op_tag, op_idx, ref_beat, 2'b00};
    assign mem_req_wdata = wt_pending ? wt_wdata : 32'd0;
    assign mem_req_be    = wt_pending ? wt_be : 4'd0;
    assign beat_accept   = mem_req_valid && mem_req_ready && refilling;
    assign wt_accept     = mem_req_valid && mem_req_ready && wt_pending;

    assign hit_word  = line_words[op_idx][op_off];
    assign load_word = hit_word;

    assign cpu_resp_valid = resp_valid_r;
    assign cpu_resp_rdata = resp_data_r;

    always_comb begin
        merged_word = hit_word;
        for (int i = 0; i < 4; i = i + 1) begin
            if (op_be[i]) begin
                merged_word[8*i +: 8] = op_wdata[8*i +: 8];
            end
        end
    end

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            refilling     <= 1'b0;
            ref_beat      <= 2'd0;
            ref_word0     <= 32'd0;
            ref_word1     <= 32'd0;
            ref_word2     <= 32'd0;
            wt_pending    <= 1'b0;
            wt_addr       <= 32'd0;
            wt_wdata      <= 32'd0;
            wt_be         <= 4'd0;
            op_we         <= 1'b0;
            op_idx        <= 4'd0;
            op_tag        <= 24'd0;
            op_off        <= 2'd0;
            op_addr       <= 32'd0;
            op_wdata      <= 32'd0;
            op_be         <= 4'd0;
            op_epoch      <= 2'd0;
            resp_valid_r  <= 1'b0;
            resp_data_r   <= 32'd0;
            for (int i = 0; i < 16; i = i + 1) begin
                line_valid[i] <= 1'b0;
                line_tag[i]   <= 24'd0;
            end
        end else begin
            resp_valid_r <= 1'b0;

            if (accept) begin
                op_we    <= cpu_req_we;
                op_idx   <= req_index;
                op_tag   <= req_tag;
                op_off   <= req_off;
                op_addr  <= cpu_req_addr;
                op_wdata <= cpu_req_wdata;
                op_be    <= cpu_req_be;
                op_epoch <= flush_epoch;
                if (hit) begin
                    if (cpu_req_we) begin
                        for (int i = 0; i < 4; i = i + 1) begin
                            if (cpu_req_be[i]) begin
                                line_words[req_index][req_off][8*i +: 8] <= cpu_req_wdata[8*i +: 8];
                            end
                        end
                        wt_pending <= 1'b1;
                        wt_addr    <= cpu_req_addr;
                        wt_wdata   <= cpu_req_wdata;
                        wt_be      <= cpu_req_be;
                    end else begin
                        resp_valid_r <= 1'b1;
                        resp_data_r  <= line_words[req_index][req_off];
                    end
                end else begin
                    refilling <= 1'b1;
                    ref_beat  <= 2'd0;
                end
            end

            if (beat_accept) begin
                if (ref_beat == 2'd0) begin
                    ref_word0 <= mem_resp_data;
                    ref_beat  <= 2'd1;
                end else if (ref_beat == 2'd1) begin
                    ref_word1 <= mem_resp_data;
                    ref_beat  <= 2'd2;
                end else if (ref_beat == 2'd2) begin
                    ref_word2 <= mem_resp_data;
                    ref_beat  <= 2'd3;
                end else begin
                    refilling <= 1'b0;
                    ref_beat  <= 2'd0;
                    if (op_epoch == flush_epoch) begin
                        line_valid[op_idx] <= 1'b1;
                        line_tag[op_idx]   <= op_tag;
                        line_words[op_idx][0] <= ref_word0;
                        line_words[op_idx][1] <= ref_word1;
                        line_words[op_idx][2] <= ref_word2;
                        line_words[op_idx][3] <= mem_resp_data;
                        if (op_we) begin
                            for (int i = 0; i < 4; i = i + 1) begin
                                if (op_be[i]) begin
                                    line_words[op_idx][op_off][8*i +: 8] <= op_wdata[8*i +: 8];
                                end
                            end
                            wt_pending <= 1'b1;
                            wt_addr    <= op_addr;
                            wt_wdata   <= op_wdata;
                            wt_be      <= op_be;
                        end else begin
                            resp_valid_r <= 1'b1;
                            resp_data_r  <= (op_off == 2'd0) ? ref_word0 :
                                            (op_off == 2'd1) ? ref_word1 :
                                            (op_off == 2'd2) ? ref_word2 :
                                                                mem_resp_data;
                        end
                    end
                end
            end

            if (wt_accept) begin
                wt_pending   <= 1'b0;
                resp_valid_r <= 1'b1;
                resp_data_r  <= 32'd0;
            end
        end
    end

endmodule
