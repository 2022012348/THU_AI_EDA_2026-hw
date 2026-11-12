module if_stage (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        if_redirect_valid,
    input  logic [31:0] if_redirect_pc,
    input  logic [1:0]  if_redirect_epoch,
    input  logic        if_hold,
    input  logic        if_flush,
    input  logic [31:0] bp_lookup_next_pc,
    input  logic        bp_lookup_valid,
    output logic [31:0] bp_lookup_pc,
    output logic        icache_cpu_req_valid,
    output logic [31:0] icache_cpu_req_addr,
    input  logic        icache_cpu_req_ready,
    input  logic        icache_cpu_resp_valid,
    input  logic [31:0] icache_cpu_resp_data,
    output logic [1:0]  icache_flush_epoch,
    output logic        if_id_valid,
    output logic [31:0] if_id_pc,
    output logic [31:0] if_id_insn,
    output logic [31:0] if_id_pred_next_pc,
    output logic        if_id_pred_valid,
    output logic [1:0]  if_id_epoch,
    output logic [1:0]  fetch_epoch,
    output logic [31:0] fetch_pc
);

    logic [31:0] pc;
    logic [1:0]  epoch;

    // outstanding request metadata
    logic        req_outstanding;
    logic [31:0] req_pc;
    logic [31:0] req_pred_next_pc;
    logic        req_pred_valid;
    logic [1:0]  req_epoch;

    // response slot
    logic        resp_slot_valid;
    logic [31:0] resp_slot_insn;
    logic [31:0] resp_slot_pc;
    logic [31:0] resp_slot_pred_next_pc;
    logic        resp_slot_pred_valid;
    logic [1:0]  resp_slot_epoch;

    logic        req_accept;
    logic        resp_accept;
    logic        slot_consume;

    assign bp_lookup_pc = pc;

    assign icache_cpu_req_valid = ~req_outstanding & ~resp_slot_valid & ~if_hold & ~if_flush & rst_n;
    assign icache_cpu_req_addr  = pc;
    assign icache_flush_epoch   = epoch;

    assign req_accept  = icache_cpu_req_valid & icache_cpu_req_ready;
    assign resp_accept = icache_cpu_resp_valid & req_outstanding;
    assign slot_consume = resp_slot_valid & ~if_hold & ~if_flush;

    assign if_id_valid        = resp_slot_valid;
    assign if_id_pc           = resp_slot_pc;
    assign if_id_insn         = resp_slot_insn;
    assign if_id_pred_next_pc = resp_slot_pred_next_pc;
    assign if_id_pred_valid   = resp_slot_pred_valid;
    assign if_id_epoch        = resp_slot_epoch;

    assign fetch_epoch = epoch;
    assign fetch_pc    = pc;

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            pc                  <= 32'b0;
            epoch               <= 2'b0;
            req_outstanding     <= 1'b0;
            req_pc              <= 32'b0;
            req_pred_next_pc    <= 32'b0;
            req_pred_valid      <= 1'b0;
            req_epoch           <= 2'b0;
            resp_slot_valid     <= 1'b0;
            resp_slot_insn      <= 32'b0;
            resp_slot_pc        <= 32'b0;
            resp_slot_pred_next_pc <= 32'b0;
            resp_slot_pred_valid   <= 1'b0;
            resp_slot_epoch     <= 2'b0;
        end else begin
            // redirect: update PC and epoch immediately, cancel outstanding
            if (if_redirect_valid) begin
                pc              <= if_redirect_pc;
                epoch           <= if_redirect_epoch;
                req_outstanding <= 1'b0;
                resp_slot_valid <= 1'b0;
            end else begin
                // request acceptance
                if (req_accept) begin
                    req_outstanding  <= 1'b1;
                    req_pc           <= pc;
                    req_pred_next_pc <= bp_lookup_next_pc;
                    req_pred_valid   <= bp_lookup_valid;
                    req_epoch        <= epoch;
                    if (bp_lookup_valid)
                        pc <= bp_lookup_next_pc;
                    else
                        pc <= pc + 32'd4;
                end

                // response capture
                if (resp_accept) begin
                    req_outstanding <= 1'b0;
                    if (req_epoch == epoch) begin
                        resp_slot_valid        <= 1'b1;
                        resp_slot_insn         <= icache_cpu_resp_data;
                        resp_slot_pc           <= req_pc;
                        resp_slot_pred_next_pc <= req_pred_next_pc;
                        resp_slot_pred_valid   <= req_pred_valid;
                        resp_slot_epoch        <= req_epoch;
                    end
                end

                // slot consumption
                if (slot_consume) begin
                    resp_slot_valid <= 1'b0;
                end
            end
        end
    end

endmodule
