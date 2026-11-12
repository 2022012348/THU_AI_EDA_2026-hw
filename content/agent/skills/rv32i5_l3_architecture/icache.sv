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

    logic        valid_arr [0:15];
    logic [23:0] tag_arr   [0:15];
    logic [31:0] data_arr  [0:15][0:3];

    logic        busy_q;
    logic        cancel_q;
    logic [1:0]  refill_idx_q;
    logic [31:0] refill_base_q;
    logic [3:0]  refill_line_q;
    logic [23:0] refill_tag_q;
    logic [31:0] refill_word_q [0:3];
    logic [1:0]  refill_epoch_q;
    logic        resp_valid_q;
    logic [31:0] resp_data_q;
    logic [1:0]  resp_word_q;

    logic [3:0]  req_index;
    logic [23:0] req_tag;
    logic [1:0]  req_word;
    logic        hit;
    logic        req_accept;
    logic        mem_accept;
    logic        refill_done;
    logic [31:0] refill_addr;

    assign req_index = cpu_req_addr[7:4];
    assign req_tag   = cpu_req_addr[31:8];
    assign req_word  = cpu_req_addr[3:2];

    assign hit = valid_arr[req_index] && (tag_arr[req_index] == req_tag);

    assign cpu_req_ready = !busy_q && !resp_valid_q;

    assign req_accept = cpu_req_valid && cpu_req_ready;

    assign mem_req_valid = busy_q;
    assign refill_addr   = refill_base_q + {refill_idx_q, 2'b00};
    assign mem_req_addr  = refill_addr;

    assign mem_accept = mem_req_valid && mem_req_ready;

    assign refill_done = mem_accept && (refill_idx_q == 2'd3);

    assign cpu_resp_valid = resp_valid_q;
    assign cpu_resp_data  = resp_data_q;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            for (int i = 0; i < 16; i = i + 1)
                valid_arr[i] <= 1'b0;
            busy_q        <= 1'b0;
            cancel_q      <= 1'b0;
            refill_idx_q  <= 2'd0;
            refill_base_q <= 32'd0;
            refill_line_q <= 4'd0;
            refill_tag_q  <= 24'd0;
            refill_epoch_q<= 2'd0;
            resp_valid_q  <= 1'b0;
            resp_data_q   <= 32'd0;
            resp_word_q   <= 2'd0;
            for (int j = 0; j < 4; j = j + 1)
                refill_word_q[j] <= 32'd0;
        end else begin
            resp_valid_q <= 1'b0;

            if (busy_q && (flush || cancel_q)) begin
                if (mem_accept) begin
                    busy_q <= 1'b0;
                    cancel_q <= 1'b0;
                end else begin
                    cancel_q <= 1'b1;
                end
            end else if (req_accept && !flush) begin
                if (hit) begin
                    resp_valid_q <= 1'b1;
                    resp_data_q  <= data_arr[req_index][req_word];
                end else begin
                    busy_q        <= 1'b1;
                    refill_idx_q  <= 2'd0;
                    refill_base_q <= {cpu_req_addr[31:4], 4'b0000};
                    refill_line_q <= req_index;
                    refill_tag_q  <= req_tag;
                    refill_epoch_q<= flush_epoch;
                    resp_word_q   <= req_word;
                end
            end

            if (mem_accept && busy_q && !flush && !cancel_q) begin
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
                        resp_valid_q <= 1'b1;
                        case (resp_word_q)
                            2'd0: resp_data_q <= refill_word_q[0];
                            2'd1: resp_data_q <= refill_word_q[1];
                            2'd2: resp_data_q <= refill_word_q[2];
                            default: resp_data_q <= mem_resp_data;
                        endcase
                    end
                end else begin
                    refill_idx_q <= refill_idx_q + 2'd1;
                end
            end
        end
    end

endmodule
