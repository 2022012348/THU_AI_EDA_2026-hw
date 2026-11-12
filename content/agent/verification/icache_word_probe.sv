`timescale 1ns/1ps
module icache_word_probe;
    logic clk = 0;
    always #5 clk = ~clk;
    logic rst_n = 0;
    logic req_valid = 0;
    logic [31:0] req_addr = 0;
    wire req_ready, resp_valid, bus_valid;
    wire [31:0] resp_data, bus_addr;

    function automatic [31:0] word_at(input [31:0] addr);
        word_at = 32'h5a003c00 ^ addr;
    endfunction

    icache dut (
        .clk(clk), .rst_n(rst_n),
        .cpu_req_valid(req_valid), .cpu_req_addr(req_addr), .cpu_req_ready(req_ready),
        .cpu_resp_valid(resp_valid), .cpu_resp_data(resp_data),
        .flush(1'b0), .flush_epoch(2'b0),
        .mem_req_valid(bus_valid), .mem_req_addr(bus_addr), .mem_req_ready(1'b1),
        .mem_resp_valid(bus_valid), .mem_resp_data(word_at(bus_addr))
    );

    task automatic check_word(input integer lane);
        integer age, read_beats;
        logic accepted;
        logic [31:0] received;
        logic [31:0] requested_addr;
        logic seen, refill_match;
        logic [31:0] first_refill;
        begin
            @(negedge clk);
            rst_n = 0;
            req_valid = 0;
            repeat (2) @(negedge clk);
            rst_n = 1;
            requested_addr = 32'h100 + 4 * lane;
            req_addr = requested_addr;
            req_valid = 1;
            accepted = 0;
            seen = 0;
            received = 32'hxxxxxxxx;
            read_beats = 0;
            refill_match = 1;
            first_refill = 32'hxxxxxxxx;
            for (age = 0; age < 32 && !seen; age = age + 1) begin
                @(posedge clk);
                if (req_valid && req_ready) accepted = 1;
                if (bus_valid === 1'b1) begin
                    if (read_beats == 0) first_refill = bus_addr;
                    if (bus_addr !== 32'h100 + 4 * read_beats) refill_match = 0;
                    read_beats = read_beats + 1;
                end
                #1;
                if (accepted && resp_valid === 1'b1) begin
                    seen = 1;
                    received = resp_data;
                end
                @(negedge clk);
                if (accepted) begin
                    req_valid = 0;
                    req_addr = 32'h100 + 4 * ((lane + 1) % 4);
                end
            end
            $display("WORD|address=%h|accepted=%b|response=%b|expected=%h|actual=%h|first_refill=%h|read_beats=%0d|refill_match=%b|match=%b",
                     requested_addr, accepted, seen, word_at(requested_addr), received,
                     first_refill, read_beats, refill_match && read_beats == 4,
                     seen && (received === word_at(requested_addr)));
        end
    endtask

    initial begin
        check_word(0);
        check_word(1);
        check_word(2);
        check_word(3);
        $finish;
    end
endmodule
