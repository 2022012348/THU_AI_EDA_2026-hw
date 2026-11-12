`timescale 1ns/1ps
module dcache_lane_probe;
    logic clk = 0;
    always #5 clk = ~clk;
    logic rst_n = 0;
    logic req_valid = 0, req_we = 0;
    logic [31:0] req_addr = 0, req_wdata = 0;
    logic [3:0] req_be = 0;
    wire req_ready, resp_valid, bus_valid, bus_we;
    wire [31:0] resp_data, bus_addr, bus_wdata;
    wire [3:0] bus_be;
    logic [31:0] backing [0:15];
    integer reads = 0, writes = 0;
    logic [31:0] refill_addresses [0:3];
    logic [31:0] last_write_addr = 0, last_write_data = 0;
    logic [3:0] last_write_be = 0;

    dcache dut (
        .clk(clk), .rst_n(rst_n),
        .cpu_req_valid(req_valid), .cpu_req_we(req_we), .cpu_req_addr(req_addr),
        .cpu_req_wdata(req_wdata), .cpu_req_be(req_be), .cpu_req_ready(req_ready),
        .cpu_resp_valid(resp_valid), .cpu_resp_rdata(resp_data),
        .flush(1'b0), .flush_epoch(2'b0),
        .mem_req_valid(bus_valid), .mem_req_we(bus_we), .mem_req_addr(bus_addr),
        .mem_req_wdata(bus_wdata), .mem_req_be(bus_be), .mem_req_ready(1'b1),
        .mem_resp_valid(bus_valid), .mem_resp_data(backing[bus_addr[5:2]])
    );

    always @(posedge clk) if (rst_n && bus_valid) begin
        if (bus_we) begin
            writes <= writes + 1;
            last_write_addr <= bus_addr;
            last_write_data <= bus_wdata;
            last_write_be <= bus_be;
            for (integer lane = 0; lane < 4; lane = lane + 1)
                if (bus_be[lane])
                    backing[bus_addr[5:2]][8*lane +: 8] <= bus_wdata[8*lane +: 8];
        end else begin
            if (reads < 4) refill_addresses[reads] <= bus_addr;
            reads <= reads + 1;
        end
    end

    task automatic transaction(
        input string label,
        input logic we,
        input logic [31:0] addr, wdata,
        input logic [3:0] be,
        input logic [31:0] expected
    );
        integer age, reads_before, writes_before;
        logic accepted, seen, matched, refill_addresses_match;
        logic [31:0] received;
        logic [31:0] line_base;
        begin
            @(negedge clk);
            reads_before = reads;
            writes_before = writes;
            req_valid = 1;
            req_we = we;
            req_addr = addr;
            req_wdata = wdata;
            req_be = be;
            accepted = 0;
            seen = 0;
            received = 32'hxxxxxxxx;
            for (age = 0; age < 48 && !seen; age = age + 1) begin
                @(posedge clk);
                if (req_valid && req_ready) accepted = 1;
                #1;
                if (accepted && resp_valid === 1'b1) begin
                    seen = 1;
                    received = resp_data;
                end
                @(negedge clk);
                if (accepted) req_valid = 0;
            end
            req_valid = 0;
            line_base = {addr[31:4], 4'b0};
            refill_addresses_match = 1;
            if (label == "cold_load")
                for (integer beat = 0; beat < 4; beat = beat + 1)
                    if (refill_addresses[beat] !== line_base + 4 * beat)
                        refill_addresses_match = 0;
            matched = seen && (we ?
                (writes == writes_before + 1 && last_write_addr === addr &&
                 last_write_data === wdata && last_write_be === be) :
                (received === expected && writes == writes_before && refill_addresses_match &&
                 reads == reads_before + ((label == "cold_load") ? 4 : 0)));
            $display("DCACHE|case=%s|address=%h|accepted=%b|response=%b|expected=%h|actual=%h|read_beats=%0d|write_beats=%0d|bus_write_addr=%h|bus_write_data=%h|bus_write_be=%b|first_refill_addr=%h|refill_addresses_match=%b|match=%b",
                     label, addr, accepted, seen, expected, received,
                     reads - reads_before, writes - writes_before,
                     last_write_addr, last_write_data, last_write_be, refill_addresses[0], refill_addresses_match, matched);
        end
    endtask

    initial begin
        for (integer index = 0; index < 16; index = index + 1)
            backing[index] = 32'h11223344 ^ index;
        repeat (3) @(negedge clk);
        rst_n = 1;
        transaction("cold_load",     0, 32'h204, 0,              0,       32'h11223345);
        transaction("halfword_hit",  1, 32'h206, 32'habcdabcd,   4'b1100, 0);
        transaction("load_after_sh", 0, 32'h204, 0,              0,       32'habcd3345);
        transaction("byte_hit",      1, 32'h205, 32'h7e7e7e7e,   4'b0010, 0);
        transaction("load_after_sb", 0, 32'h204, 0,              0,       32'habcd7e45);
        transaction("byte_miss",     1, 32'h225, 32'haaaaaaaa,   4'b0010, 0);
        transaction("load_after_miss_store", 0, 32'h224, 0,    0,       32'h1122aa4d);
        if ($test$plusargs("final_beat_store")) begin
            transaction("final_beat_store_miss", 1, 32'h23f, 32'hdddddddd, 4'b1000, 0);
            transaction("load_after_final_beat_store", 0, 32'h23c, 0, 0,    32'hdd22334b);
        end
        $finish;
    end
endmodule
