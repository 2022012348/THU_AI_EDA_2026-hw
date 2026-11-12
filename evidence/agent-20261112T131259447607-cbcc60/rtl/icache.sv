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

    localparam logic [1:0] C_IDLE  = 2'b00;
    localparam logic [1:0] C_REFILL = 2'b01;
    localparam logic [1:0] C_RESP  = 2'b10;

    logic [31:0] line_data [0:15][0:3];
    logic [31:0] line_tag  [0:15];
    logic        line_valid[0:15];

    logic [1:0]  state;
    logic [3:0]  refill_idx;
    logic [31:0] refill_base;
    logic [3:0]  refill_line;
    logic [31:0] refill_tag;
    logic [31:0] refill_words [0:3];
    logic [1:0]  req_epoch;
    logic [1:0]  req_word_off;
    logic [31:0] req_addr_saved;
    logic [31:0] resp_data;
    logic        resp_valid_r;

    logic [3:0]  req_index;
    logic [31:0] req_tag;
    logic        hit;

    assign req_index = cpu_req_addr[7:4];
    assign req_tag = cpu_req_addr[31:8];
    assign hit = line_valid[req_index] && (line_tag[req_index] == req_tag);

    assign cpu_req_ready = (state == C_IDLE);
    assign cpu_resp_valid = resp_valid_r;
    assign cpu_resp_data = resp_data;

    assign mem_req_valid = (state == C_REFILL);
    assign mem_req_addr = refill_base + {refill_idx, 2'b00};

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state <= C_IDLE;
            refill_idx <= 4'h0;
            refill_base <= 32'h0;
            refill_line <= 4'h0;
            refill_tag <= 32'h0;
            req_epoch <= 2'b00;
            req_word_off <= 2'b00;
            req_addr_saved <= 32'h0;
            resp_valid_r <= 1'b0;
            resp_data <= 32'h0;
            for (int i = 0; i < 16; i = i + 1) begin
                line_valid[i] <= 1'b0;
                line_tag[i] <= 32'h0;
                line_data[i][0] <= 32'h0;
                line_data[i][1] <= 32'h0;
                line_data[i][2] <= 32'h0;
                line_data[i][3] <= 32'h0;
            end
        end else begin
            resp_valid_r <= 1'b0;
            case (state)
                C_IDLE: begin
                    if (cpu_req_valid && cpu_req_ready) begin
                        req_epoch <= flush_epoch;
                        req_word_off <= cpu_req_addr[3:2];
                        req_addr_saved <= cpu_req_addr;
                        if (hit) begin
                            resp_data <= line_data[req_index][cpu_req_addr[3:2]];
                            resp_valid_r <= 1'b1;
                        end else begin
                            refill_base <= {cpu_req_addr[31:4], 4'b0000};
                            refill_line <= cpu_req_addr[7:4];
                            refill_tag <= cpu_req_addr[31:8];
                            refill_idx <= 4'h0;
                            state <= C_REFILL;
                        end
                    end
                end
                C_REFILL: begin
                    if (mem_req_valid && mem_req_ready) begin
                        refill_words[refill_idx[1:0]] <= mem_resp_data;
                        if (refill_idx == 4'd3) begin
                            line_data[refill_line][0] <= refill_words[0];
                            line_data[refill_line][1] <= refill_words[1];
                            line_data[refill_line][2] <= refill_words[2];
                            line_data[refill_line][3] <= mem_resp_data;
                            line_tag[refill_line] <= refill_tag;
                            line_valid[refill_line] <= 1'b1;
                            if (req_epoch == flush_epoch) begin
                                case (req_word_off)
                                    2'b00: resp_data <= refill_words[0];
                                    2'b01: resp_data <= refill_words[1];
                                    2'b10: resp_data <= refill_words[2];
                                    default: resp_data <= mem_resp_data;
                                endcase
                                resp_valid_r <= 1'b1;
                            end
                            state <= C_IDLE;
                        end else begin
                            refill_idx <= refill_idx + 4'h1;
                        end
                    end
                end
                default: state <= C_IDLE;
            endcase
        end
    end

endmodule
