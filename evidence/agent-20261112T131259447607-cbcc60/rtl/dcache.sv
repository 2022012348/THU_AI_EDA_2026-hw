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

    localparam logic [1:0] D_IDLE   = 2'b00;
    localparam logic [1:0] D_REFILL = 2'b01;
    localparam logic [1:0] D_WRITE  = 2'b10;
    localparam logic [1:0] D_RESP   = 2'b11;

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
    logic [31:0] req_wdata_saved;
    logic [3:0]  req_be_saved;
    logic        req_we_saved;
    logic [31:0] resp_data;
    logic        resp_valid_r;

    logic [3:0]  req_index;
    logic [31:0] req_tag;
    logic        hit;

    assign req_index = cpu_req_addr[7:4];
    assign req_tag = cpu_req_addr[31:8];
    assign hit = line_valid[req_index] && (line_tag[req_index] == req_tag);

    assign cpu_req_ready = (state == D_IDLE);
    assign cpu_resp_valid = resp_valid_r;
    assign cpu_resp_rdata = resp_data;

    assign mem_req_valid = (state == D_REFILL) || (state == D_WRITE);
    assign mem_req_addr = (state == D_REFILL) ? (refill_base + {refill_idx, 2'b00}) : req_addr_saved;
    assign mem_req_we = (state == D_WRITE);
    assign mem_req_wdata = req_wdata_saved;
    assign mem_req_be = req_be_saved;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state <= D_IDLE;
            refill_idx <= 4'h0;
            refill_base <= 32'h0;
            refill_line <= 4'h0;
            refill_tag <= 32'h0;
            req_epoch <= 2'b00;
            req_word_off <= 2'b00;
            req_addr_saved <= 32'h0;
            req_wdata_saved <= 32'h0;
            req_be_saved <= 4'h0;
            req_we_saved <= 1'b0;
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
                D_IDLE: begin
                    if (cpu_req_valid && cpu_req_ready) begin
                        req_epoch <= flush_epoch;
                        req_word_off <= cpu_req_addr[3:2];
                        req_addr_saved <= cpu_req_addr;
                        req_wdata_saved <= cpu_req_wdata;
                        req_be_saved <= cpu_req_be;
                        req_we_saved <= cpu_req_we;
                        if (hit) begin
                            if (cpu_req_we) begin
                                if (cpu_req_be[0]) line_data[req_index][cpu_req_addr[3:2]][7:0] <= cpu_req_wdata[7:0];
                                if (cpu_req_be[1]) line_data[req_index][cpu_req_addr[3:2]][15:8] <= cpu_req_wdata[15:8];
                                if (cpu_req_be[2]) line_data[req_index][cpu_req_addr[3:2]][23:16] <= cpu_req_wdata[23:16];
                                if (cpu_req_be[3]) line_data[req_index][cpu_req_addr[3:2]][31:24] <= cpu_req_wdata[31:24];
                                state <= D_WRITE;
                            end else begin
                                resp_data <= line_data[req_index][cpu_req_addr[3:2]];
                                resp_valid_r <= 1'b1;
                            end
                        end else begin
                            refill_base <= {cpu_req_addr[31:4], 4'b0000};
                            refill_line <= cpu_req_addr[7:4];
                            refill_tag <= cpu_req_addr[31:8];
                            refill_idx <= 4'h0;
                            state <= D_REFILL;
                        end
                    end
                end
                D_REFILL: begin
                    if (mem_req_valid && mem_req_ready) begin
                        refill_words[refill_idx[1:0]] <= mem_resp_data;
                        if (refill_idx == 4'd3) begin
                            line_data[refill_line][0] <= refill_words[0];
                            line_data[refill_line][1] <= refill_words[1];
                            line_data[refill_line][2] <= refill_words[2];
                            line_data[refill_line][3] <= mem_resp_data;
                            if (req_we_saved) begin
                                if (req_word_off == 2'b00) begin
                                    if (req_be_saved[0]) line_data[refill_line][0][7:0]   <= req_wdata_saved[7:0];
                                    if (req_be_saved[1]) line_data[refill_line][0][15:8]  <= req_wdata_saved[15:8];
                                    if (req_be_saved[2]) line_data[refill_line][0][23:16] <= req_wdata_saved[23:16];
                                    if (req_be_saved[3]) line_data[refill_line][0][31:24] <= req_wdata_saved[31:24];
                                end else if (req_word_off == 2'b01) begin
                                    if (req_be_saved[0]) line_data[refill_line][1][7:0]   <= req_wdata_saved[7:0];
                                    if (req_be_saved[1]) line_data[refill_line][1][15:8]  <= req_wdata_saved[15:8];
                                    if (req_be_saved[2]) line_data[refill_line][1][23:16] <= req_wdata_saved[23:16];
                                    if (req_be_saved[3]) line_data[refill_line][1][31:24] <= req_wdata_saved[31:24];
                                end else if (req_word_off == 2'b10) begin
                                    if (req_be_saved[0]) line_data[refill_line][2][7:0]   <= req_wdata_saved[7:0];
                                    if (req_be_saved[1]) line_data[refill_line][2][15:8]  <= req_wdata_saved[15:8];
                                    if (req_be_saved[2]) line_data[refill_line][2][23:16] <= req_wdata_saved[23:16];
                                    if (req_be_saved[3]) line_data[refill_line][2][31:24] <= req_wdata_saved[31:24];
                                end else begin
                                    if (req_be_saved[0]) line_data[refill_line][3][7:0]   <= req_wdata_saved[7:0];
                                    if (req_be_saved[1]) line_data[refill_line][3][15:8]  <= req_wdata_saved[15:8];
                                    if (req_be_saved[2]) line_data[refill_line][3][23:16] <= req_wdata_saved[23:16];
                                    if (req_be_saved[3]) line_data[refill_line][3][31:24] <= req_wdata_saved[31:24];
                                end
                            end
                            line_tag[refill_line] <= refill_tag;
                            line_valid[refill_line] <= 1'b1;
                            if (req_we_saved) begin
                                state <= D_WRITE;
                            end else begin
                                if (req_epoch == flush_epoch) begin
                                    case (req_word_off)
                                        2'b00: resp_data <= refill_words[0];
                                        2'b01: resp_data <= refill_words[1];
                                        2'b10: resp_data <= refill_words[2];
                                        default: resp_data <= mem_resp_data;
                                    endcase
                                    resp_valid_r <= 1'b1;
                                end
                                state <= D_IDLE;
                            end
                        end else begin
                            refill_idx <= refill_idx + 4'h1;
                        end
                    end
                end
                D_WRITE: begin
                    if (mem_req_valid && mem_req_ready) begin
                        if (req_epoch == flush_epoch) begin
                            resp_valid_r <= 1'b1;
                            resp_data <= 32'h0;
                        end
                        state <= D_IDLE;
                    end
                end
                default: state <= D_IDLE;
            endcase
        end
    end

endmodule
