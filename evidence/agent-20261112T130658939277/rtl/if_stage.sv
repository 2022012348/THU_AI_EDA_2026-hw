module if_stage (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        if_id_hold,
    input  logic        if_id_flush,
    input  logic        redirect_valid,
    input  logic [31:0] redirect_pc,
    input  logic [1:0]  redirect_epoch,
    input  logic        halt_pending,
    output logic [31:0] if_pc,
    output logic        if_id_valid,
    output logic [31:0] if_id_pc,
    output logic [31:0] if_id_insn,
    output logic        cpu_req_valid,
    output logic [31:0] cpu_req_addr,
    input  logic        cpu_req_ready,
    input  logic        cpu_resp_valid,
    input  logic [31:0] cpu_resp_data,
    output logic [1:0]  if_epoch,
    output logic [1:0]  if_flush_epoch,
    output logic        if_flush
);

    localparam logic [1:0] ST_IDLE    = 2'd0;
    localparam logic [1:0] ST_AWAIT   = 2'd1;
    localparam logic [1:0] ST_HOLD    = 2'd2;

    logic [1:0]  state;
    logic [31:0] pc_r;
    logic [1:0]  epoch_r;
    logic [31:0] saved_pc;
    logic [1:0]  saved_epoch;
    logic [31:0] ifid_pc_r;
    logic [31:0] ifid_insn_r;
    logic        ifid_valid_r;

    logic        req_fire;
    logic        resp_fire;
    logic        ifid_consume;
    logic        redirect_fire;

    assign if_pc          = pc_r;
    assign if_epoch       = epoch_r;
    assign if_id_valid    = ifid_valid_r;
    assign if_id_pc       = ifid_pc_r;
    assign if_id_insn     = ifid_insn_r;
    assign cpu_req_valid  = (state == ST_IDLE) && !halt_pending && !redirect_valid;
    assign cpu_req_addr   = pc_r;
    assign if_flush_epoch = redirect_epoch;
    assign if_flush       = redirect_valid;

    assign req_fire      = cpu_req_valid && cpu_req_ready;
    assign resp_fire     = (state == ST_AWAIT) && cpu_resp_valid;
    assign ifid_consume  = ifid_valid_r && !if_id_hold;
    assign redirect_fire = redirect_valid;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            state        <= ST_IDLE;
            pc_r         <= 32'b0;
            epoch_r      <= 2'b0;
            saved_pc     <= 32'b0;
            saved_epoch  <= 2'b0;
            ifid_pc_r    <= 32'b0;
            ifid_insn_r  <= 32'b0;
            ifid_valid_r <= 1'b0;
        end else if (redirect_fire) begin
            state        <= ST_IDLE;
            pc_r         <= redirect_pc;
            epoch_r      <= redirect_epoch;
            ifid_valid_r <= 1'b0;
        end else begin
            // IF/ID slot management
            if (if_id_flush) begin
                ifid_valid_r <= 1'b0;
            end else if (ifid_consume) begin
                ifid_valid_r <= 1'b0;
            end

            // Response capture into IF/ID (even if held)
            if (resp_fire) begin
                ifid_valid_r <= 1'b1;
                ifid_pc_r    <= saved_pc;
                ifid_insn_r  <= cpu_resp_data;
            end

            // State machine
            case (state)
                ST_IDLE: begin
                    if (req_fire) begin
                        saved_pc    <= pc_r;
                        saved_epoch <= epoch_r;
                        pc_r        <= pc_r + 32'd4;
                        state       <= ST_AWAIT;
                    end
                end
                ST_AWAIT: begin
                    if (resp_fire) begin
                        state <= ST_HOLD;
                    end
                end
                ST_HOLD: begin
                    if (ifid_consume) begin
                        state <= ST_IDLE;
                    end
                end
                default: state <= ST_IDLE;
            endcase
        end
    end

endmodule
