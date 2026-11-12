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

    localparam logic [1:0] C_IDLE  = 2'd0;
    localparam logic [1:0] C_REFILL= 2'd1;
    localparam logic [1:0] C_RESP  = 2'd2;

    logic [31:0] data_arr [0:15][0:3];
    logic [15:0] valid_arr;
    logic [23:0] tag_arr [0:15];

    logic [1:0]  cstate;
    logic [3:0]  refill_idx;
    logic [31:0] refill_base;
    logic [3:0]  refill_line;
    logic [31:0] refill_words [0:3];
    logic [31:0] req_addr_r;
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

    assign cpu_req_ready  = (cstate == C_IDLE) && !resp_valid_r;
    assign cpu_resp_valid = resp_valid_r;
    assign cpu_resp_data  = resp_data_r;

    assign mem_req_valid = (cstate == C_REFILL);
    assign mem_req_addr  = refill_base + {refill_idx, 2'b00};

    logic        req_fire;
    logic        refill_fire;
    logic        refill_done;
    assign req_fire    = cpu_req_valid && cpu_req_ready;
    assign refill_fire = mem_req_valid && mem_req_ready;
    assign refill_done = refill_fire && (refill_idx == 4'd3);

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            cstate       <= C_IDLE;
            valid_arr    <= 16'b0;
            refill_idx   <= 4'b0;
            refill_base  <= 32'b0;
            refill_line  <= 4'b0;
            req_addr_r   <= 32'b0;
            resp_data_r  <= 32'b0;
            resp_valid_r <= 1'b0;
        end else begin
            resp_valid_r <= 1'b0;

            if (flush) begin
                // cancel pending refill; keep valid lines
                if (cstate == C_REFILL) begin
                    cstate <= C_IDLE;
                end
            end

            case (cstate)
                C_IDLE: begin
                    if (req_fire) begin
                        req_addr_r <= cpu_req_addr;
                        if (hit) begin
                            resp_data_r  <= data_arr[req_idx][req_word];
                            resp_valid_r <= 1'b1;
                        end else begin
                            refill_base <= {cpu_req_addr[31:4], 4'b0000};
                            refill_line <= req_idx;
                            refill_idx  <= 4'b0;
                            cstate      <= C_REFILL;
                        end
                    end
                end
                C_REFILL: begin
                    if (refill_fire) begin
                        refill_words[refill_idx] <= mem_resp_data;
                        if (refill_idx == 4'd3) begin
                            data_arr[refill_line][0] <= refill_words[0];
                            data_arr[refill_line][1] <= refill_words[1];
                            data_arr[refill_line][2] <= refill_words[2];
                            data_arr[refill_line][3] <= mem_resp_data;
                            tag_arr[refill_line]     <= refill_base[31:8];
                            valid_arr[refill_line]   <= 1'b1;
                            resp_data_r  <= (req_addr_r[3:2] == 2'd0) ? refill_words[0] :
                                            (req_addr_r[3:2] == 2'd1) ? refill_words[1] :
                                            (req_addr_r[3:2] == 2'd2) ? refill_words[2] :
                                            mem_resp_data;
                            resp_valid_r <= 1'b1;
                            cstate       <= C_IDLE;
                        end else begin
                            refill_idx <= refill_idx + 4'd1;
                        end
                    end
                end
                default: cstate <= C_IDLE;
            endcase
        end
    end

endmodule
