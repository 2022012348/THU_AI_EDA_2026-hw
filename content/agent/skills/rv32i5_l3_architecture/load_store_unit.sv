module load_store_unit (
    input logic clk, rst_n, squash, enqueue,
    input logic [2:0] disp_rob_idx, rob_head,
    input logic [5:0] enq_prs1, enq_prs2, enq_prd,
    input logic [31:0] enq_imm,
    input logic [2:0] enq_funct3,
    input logic enq_store,
    input logic [39:0] ready_bits,
    output logic [5:0] lsu_rs1_idx, lsu_rs2_idx,
    input logic [31:0] lsu_rs1_value, lsu_rs2_value,
    output logic enq_ready,
    output logic cpu_req_valid, cpu_req_we,
    output logic [31:0] cpu_req_addr, cpu_req_wdata,
    output logic [3:0] cpu_req_be,
    input logic cpu_req_ready, cpu_resp_valid,
    input logic [31:0] cpu_resp_rdata,
    output logic complete_valid, wait_load,
    output logic [2:0] complete_tag,
    output logic [5:0] complete_prd,
    output logic [31:0] complete_value
);
    logic read_slot, write_slot, pending;
    logic [1:0] count;
    logic [2:0] tag [0:1], funct3 [0:1];
    logic [5:0] prs1 [0:1], prs2 [0:1], prd [0:1];
    logic [31:0] imm [0:1];
    logic store [0:1];
    logic [1:0] saved_offset;
    logic [31:0] shifted_data;

    assign enq_ready = count < 2;
    assign lsu_rs1_idx = count != 0 ? prs1[read_slot] : 6'd0;
    assign lsu_rs2_idx = count != 0 ? prs2[read_slot] : 6'd0;
    assign cpu_req_valid = count != 0 && !pending && tag[read_slot] == rob_head
                          && ready_bits[lsu_rs1_idx] && ready_bits[lsu_rs2_idx];
    assign cpu_req_we = store[read_slot];
    assign cpu_req_addr = lsu_rs1_value + imm[read_slot];
    assign cpu_req_wdata = funct3[read_slot] == 0 ? {4{lsu_rs2_value[7:0]}} :
                           funct3[read_slot] == 1 ? {2{lsu_rs2_value[15:0]}} : lsu_rs2_value;
    assign cpu_req_be = !cpu_req_we ? 4'b0 :
                        funct3[read_slot] == 0 ? (4'b0001 << cpu_req_addr[1:0]) :
                        funct3[read_slot] == 1 ? (cpu_req_addr[1] ? 4'b1100 : 4'b0011) : 4'b1111;
    assign complete_valid = pending && cpu_resp_valid;
    assign complete_tag = tag[read_slot];
    assign complete_prd = prd[read_slot];
    assign wait_load = pending && !store[read_slot];
    assign shifted_data = cpu_resp_rdata >> {saved_offset, 3'b0};

    always @* begin
        case (funct3[read_slot])
            3'b000: complete_value = {{24{shifted_data[7]}}, shifted_data[7:0]};
            3'b001: complete_value = {{16{shifted_data[15]}}, shifted_data[15:0]};
            3'b100: complete_value = {24'd0, shifted_data[7:0]};
            3'b101: complete_value = {16'd0, shifted_data[15:0]};
            default: complete_value = cpu_resp_rdata;
        endcase
    end
    always @(posedge clk) begin
        if (!rst_n || squash) begin
            read_slot <= 0;
            write_slot <= 0;
            count <= 0;
            pending <= 0;
            saved_offset <= 0;
        end else begin
            if (cpu_req_valid && cpu_req_ready) begin
                pending <= 1;
                saved_offset <= cpu_req_addr[1:0];
            end
            if (complete_valid) begin
                pending <= 0;
                read_slot <= ~read_slot;
            end
            if (enqueue && enq_ready) begin
                tag[write_slot] <= disp_rob_idx;
                prs1[write_slot] <= enq_prs1;
                prs2[write_slot] <= enq_prs2;
                prd[write_slot] <= enq_prd;
                imm[write_slot] <= enq_imm;
                funct3[write_slot] <= enq_funct3;
                store[write_slot] <= enq_store;
                write_slot <= ~write_slot;
            end
            case ({enqueue && enq_ready, complete_valid})
                2'b10: count <= count + 2'd1;
                2'b01: count <= count - 2'd1;
                default: ;
            endcase
        end
    end
endmodule
