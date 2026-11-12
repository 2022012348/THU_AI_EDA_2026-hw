module load_store_unit (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        squash,
    input  logic        enqueue,
    input  logic [2:0]  disp_rob_idx,
    input  logic [2:0]  rob_head,
    input  logic [5:0]  enq_prs1,
    input  logic [5:0]  enq_prs2,
    input  logic [5:0]  enq_prd,
    input  logic [31:0] enq_imm,
    input  logic [2:0]  enq_funct3,
    input  logic        enq_store,
    input  logic [39:0] ready_bits,
    output logic [5:0]  lsu_rs1_idx,
    output logic [5:0]  lsu_rs2_idx,
    input  logic [31:0] lsu_rs1_value,
    input  logic [31:0] lsu_rs2_value,
    output logic        enq_ready,
    output logic        cpu_req_valid,
    output logic        cpu_req_we,
    output logic [31:0] cpu_req_addr,
    output logic [31:0] cpu_req_wdata,
    output logic [3:0]  cpu_req_be,
    input  logic        cpu_req_ready,
    input  logic        cpu_resp_valid,
    input  logic [31:0] cpu_resp_rdata,
    output logic        complete_valid,
    output logic        wait_load,
    output logic [2:0]  complete_tag,
    output logic [5:0]  complete_prd,
    output logic [31:0] complete_value
);
    logic        ent_valid [0:1];
    logic [2:0]  ent_tag [0:1];
    logic [5:0]  ent_prs1 [0:1];
    logic [5:0]  ent_prs2 [0:1];
    logic [5:0]  ent_prd [0:1];
    logic [31:0] ent_imm [0:1];
    logic [2:0]  ent_f3 [0:1];
    logic        ent_store [0:1];

    logic        out_valid;
    logic [2:0]  out_tag;
    logic [5:0]  out_prd;
    logic        out_store;
    logic [2:0]  out_f3;
    logic [31:0] out_addr;
    logic [31:0] out_wdata;
    logic [3:0]  out_be;
    logic [5:0]  out_prs1_hold;
    logic [5:0]  out_prs2_hold;
    logic [31:0] out_imm_hold;

    logic        slot0_ok;
    logic        slot1_ok;
    logic        launch_ok;
    logic        launch_fire;
    logic        resp_fire;
    logic        pop0;
    logic        pop1;
    logic        enq_fire;
    logic [31:0] addr_calc;
    logic [31:0] store_data;
    logic [31:0] load_data;
    logic [1:0]  byte_off;
    logic        half_sel;
    logic [2:0]  sel_f3;
    logic        sel_store;
    logic [31:0] sel_imm;
    logic [5:0]  sel_prs1;
    logic [5:0]  sel_prs2;
    logic [2:0]  i;

    assign slot0_ok = ent_valid[0] && (ent_tag[0] == rob_head) &&
                      ready_bits[ent_prs1[0]] && ready_bits[ent_prs2[0]];
    assign slot1_ok = ent_valid[1] && (ent_tag[1] == rob_head) &&
                      ready_bits[ent_prs1[1]] && ready_bits[ent_prs2[1]];
    assign launch_ok = !out_valid && (slot0_ok || slot1_ok);

    assign sel_prs1 = slot0_ok ? ent_prs1[0] : ent_prs1[1];
    assign sel_prs2 = slot0_ok ? ent_prs2[0] : ent_prs2[1];
    assign sel_imm  = slot0_ok ? ent_imm[0]  : ent_imm[1];
    assign sel_f3   = slot0_ok ? ent_f3[0]   : ent_f3[1];
    assign sel_store= slot0_ok ? ent_store[0]: ent_store[1];

    assign lsu_rs1_idx = out_valid ? out_prs1_hold : sel_prs1;
    assign lsu_rs2_idx = out_valid ? out_prs2_hold : sel_prs2;

    assign addr_calc = lsu_rs1_value + (out_valid ? out_imm_hold : sel_imm);
    assign store_data = lsu_rs2_value;
    assign byte_off = addr_calc[1:0];
    assign half_sel = addr_calc[1];

    always_comb begin
        cpu_req_valid = 1'b0;
        cpu_req_we = 1'b0;
        cpu_req_addr = 32'b0;
        cpu_req_wdata = 32'b0;
        cpu_req_be = 4'b0;
        if (out_valid) begin
            cpu_req_valid = 1'b1;
            cpu_req_we = out_store;
            cpu_req_addr = out_addr;
            cpu_req_wdata = out_wdata;
            cpu_req_be = out_be;
        end else if (launch_ok) begin
            cpu_req_valid = 1'b1;
            cpu_req_we = sel_store;
            cpu_req_addr = addr_calc;
            if (sel_store) begin
                case (sel_f3)
                    3'b000: begin
                        cpu_req_wdata = {4{store_data[7:0]}};
                        cpu_req_be = 4'b0001 << byte_off;
                    end
                    3'b001: begin
                        cpu_req_wdata = {2{store_data[15:0]}};
                        cpu_req_be = half_sel ? 4'b1100 : 4'b0011;
                    end
                    default: begin
                        cpu_req_wdata = store_data;
                        cpu_req_be = 4'b1111;
                    end
                endcase
            end else begin
                cpu_req_wdata = 32'b0;
                cpu_req_be = 4'b0;
            end
        end
    end

    assign launch_fire = cpu_req_valid && cpu_req_ready && !out_valid;
    assign resp_fire = out_valid && cpu_resp_valid;

    logic [7:0]  load_byte;
    logic [15:0] load_half;

    assign load_byte = cpu_resp_rdata[8*out_addr[1:0] +: 8];
    assign load_half = cpu_resp_rdata[16*out_addr[1] +: 16];

    always_comb begin
        load_data = cpu_resp_rdata;
        case (out_f3)
            3'b000: load_data = {{24{load_byte[7]}}, load_byte};
            3'b001: load_data = {{16{load_half[15]}}, load_half};
            3'b100: load_data = {24'b0, load_byte};
            3'b101: load_data = {16'b0, load_half};
            default: load_data = cpu_resp_rdata;
        endcase
    end

    assign complete_valid = resp_fire;
    assign complete_tag = out_tag;
    assign complete_prd = out_store ? 6'b0 : out_prd;
    assign complete_value = out_store ? 32'b0 : load_data;
    assign wait_load = out_valid && !out_store;

    assign enq_ready = !ent_valid[1] || (resp_fire && !ent_valid[1]);
    assign enq_fire = enqueue && enq_ready && !squash;

    assign pop0 = resp_fire && ent_valid[0] && (ent_tag[0] == out_tag);
    assign pop1 = resp_fire && !pop0 && ent_valid[1] && (ent_tag[1] == out_tag);

    always_ff @(posedge clk) begin
        if (!rst_n) begin
            out_valid <= 1'b0;
            out_tag <= 3'b0;
            out_prd <= 6'b0;
            out_store <= 1'b0;
            out_f3 <= 3'b0;
            out_addr <= 32'b0;
            out_wdata <= 32'b0;
            out_be <= 4'b0;
            out_prs1_hold <= 6'b0;
            out_prs2_hold <= 6'b0;
            out_imm_hold <= 32'b0;
            for (i = 3'd0; i < 3'd2; i = i + 3'd1) begin
                ent_valid[i] <= 1'b0;
                ent_tag[i] <= 3'b0;
                ent_prs1[i] <= 6'b0;
                ent_prs2[i] <= 6'b0;
                ent_prd[i] <= 6'b0;
                ent_imm[i] <= 32'b0;
                ent_f3[i] <= 3'b0;
                ent_store[i] <= 1'b0;
            end
        end else begin
            if (squash) begin
                for (i = 3'd0; i < 3'd2; i = i + 3'd1) begin
                    if (ent_valid[i] && (ent_tag[i] != rob_head)) begin
                        ent_valid[i] <= 1'b0;
                    end
                end
            end
            if (launch_fire) begin
                out_valid <= 1'b1;
                out_tag <= slot0_ok ? ent_tag[0] : ent_tag[1];
                out_prd <= slot0_ok ? ent_prd[0] : ent_prd[1];
                out_store <= sel_store;
                out_f3 <= sel_f3;
                out_addr <= addr_calc;
                out_wdata <= cpu_req_wdata;
                out_be <= cpu_req_be;
                out_prs1_hold <= sel_prs1;
                out_prs2_hold <= sel_prs2;
                out_imm_hold <= sel_imm;
                if (slot0_ok) begin
                    ent_valid[0] <= 1'b0;
                    if (ent_valid[1]) begin
                        ent_valid[0] <= 1'b1;
                        ent_tag[0] <= ent_tag[1];
                        ent_prs1[0] <= ent_prs1[1];
                        ent_prs2[0] <= ent_prs2[1];
                        ent_prd[0] <= ent_prd[1];
                        ent_imm[0] <= ent_imm[1];
                        ent_f3[0] <= ent_f3[1];
                        ent_store[0] <= ent_store[1];
                        ent_valid[1] <= 1'b0;
                    end
                end else begin
                    ent_valid[1] <= 1'b0;
                end
            end else if (resp_fire) begin
                out_valid <= 1'b0;
                if (pop0) begin
                    if (ent_valid[1]) begin
                        ent_valid[0] <= 1'b1;
                        ent_tag[0] <= ent_tag[1];
                        ent_prs1[0] <= ent_prs1[1];
                        ent_prs2[0] <= ent_prs2[1];
                        ent_prd[0] <= ent_prd[1];
                        ent_imm[0] <= ent_imm[1];
                        ent_f3[0] <= ent_f3[1];
                        ent_store[0] <= ent_store[1];
                        ent_valid[1] <= 1'b0;
                    end else begin
                        ent_valid[0] <= 1'b0;
                    end
                end else if (pop1) begin
                    ent_valid[1] <= 1'b0;
                end
            end
            if (enq_fire) begin
                if (!ent_valid[0]) begin
                    ent_valid[0] <= 1'b1;
                    ent_tag[0] <= disp_rob_idx;
                    ent_prs1[0] <= enq_prs1;
                    ent_prs2[0] <= enq_prs2;
                    ent_prd[0] <= enq_prd;
                    ent_imm[0] <= enq_imm;
                    ent_f3[0] <= enq_funct3;
                    ent_store[0] <= enq_store;
                end else begin
                    ent_valid[1] <= 1'b1;
                    ent_tag[1] <= disp_rob_idx;
                    ent_prs1[1] <= enq_prs1;
                    ent_prs2[1] <= enq_prs2;
                    ent_prd[1] <= enq_prd;
                    ent_imm[1] <= enq_imm;
                    ent_f3[1] <= enq_funct3;
                    ent_store[1] <= enq_store;
                end
            end
        end
    end
endmodule
