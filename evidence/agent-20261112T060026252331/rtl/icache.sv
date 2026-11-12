module icache (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        cpu_req_valid,
    input  logic [31:0] cpu_req_addr,
    output logic        cpu_req_ready,
    output logic        cpu_resp_valid,
    output logic [31:0] cpu_resp_data,
    input  logic        flush,
    input  logic [1:0]  flush_epoch,
    output logic        mem_req_valid,
    input  logic        mem_req_ready,
    output logic [31:0] mem_req_addr,
    input  logic        mem_resp_valid,
    input  logic [31:0] mem_resp_data
);

    logic        line_valid [0:15];
    logic [23:0] line_tag   [0:15];
    logic [31:0] line_words [0:15][0:3];

    logic        busy;
    logic        ref_active;
    logic [3:0]  ref_idx;
    logic [23:0] ref_tag;
    logic [1:0]  ref_beat;
    logic [1:0]  ref_off;
    logic [1:0]  ref_epoch;
    logic [31:0] ref_word0;
    logic [31:0] ref_word1;
    logic [31:0] ref_word2;

    logic        resp_valid_r;
    logic [31:0] resp_data_r;

    logic [3:0]  req_index;
    logic [23:0] req_tag;
    logic [1:0]  req_off;
    logic        hit;
    logic        accept;
    logic        beat_accept;
    logic [31:0] refill_word;
    logic [31:0] hit_word;

    assign req_index = cpu_req_addr[7:4];
    assign req_tag   = cpu_req_addr[31:8];
    assign req_off   = cpu_req_addr[3:2];
    assign hit       = line_valid[req_index] && (line_tag[req_index] == req_tag);

    assign busy          = ref_active;
    assign cpu_req_ready = rst_n && !busy && !resp_valid_r;
    assign accept        = cpu_req_valid && cpu_req_ready;

    assign mem_req_valid = rst_n && busy;
    assign mem_req_addr  = {ref_tag, ref_idx, ref_beat, 2'b00};
    assign beat_accept   = mem_req_valid && mem_req_ready;

    assign refill_word = (ref_off == 2'd0) ? ref_word0 :
                         (ref_off == 2'd1) ? ref_word1 :
                         (ref_off == 2'd2) ? ref_word2 :
                                             mem_resp_data;
    assign hit_word    = line_words[req_index][req_off];

    assign cpu_resp_valid = resp_valid_r;
    assign cpu_resp_data  = resp_data_r;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            ref_active    <= 1'b0;
            ref_beat      <= 2'd0;
            ref_idx       <= 4'd0;
            ref_tag       <= 24'd0;
            ref_off       <= 2'd0;
            ref_epoch     <= 2'd0;
            ref_word0     <= 32'd0;
            ref_word1     <= 32'd0;
            ref_word2     <= 32'd0;
            resp_valid_r  <= 1'b0;
            resp_data_r   <= 32'd0;
            for (int i = 0; i < 16; i = i + 1) begin
                line_valid[i] <= 1'b0;
                line_tag[i]   <= 24'd0;
            end
        end else begin
            resp_valid_r <= 1'b0;

            if (accept) begin
                if (hit) begin
                    resp_valid_r <= 1'b1;
                    resp_data_r  <= hit_word;
                end else begin
                    ref_active <= 1'b1;
                    ref_beat  <= 2'd0;
                    ref_idx   <= req_index;
                    ref_tag   <= req_tag;
                    ref_off   <= req_off;
                    ref_epoch <= flush_epoch;
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
                end else if (ref_beat == 2'd3) begin
                    ref_active <= 1'b0;
                    ref_beat  <= 2'd0;
                    if (ref_epoch == flush_epoch) begin
                        line_valid[ref_idx] <= 1'b1;
                        line_tag[ref_idx]   <= ref_tag;
                        line_words[ref_idx][0] <= ref_word0;
                        line_words[ref_idx][1] <= ref_word1;
                        line_words[ref_idx][2] <= ref_word2;
                        line_words[ref_idx][3] <= mem_resp_data;
                        resp_valid_r <= 1'b1;
                        resp_data_r  <= refill_word;
                    end
                end
            end

            if (flush && busy && (ref_epoch != flush_epoch)) begin
                ref_active <= 1'b0;
                ref_beat   <= 2'd0;
            end
        end
    end

endmodule
