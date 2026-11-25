`timescale 1ns/1ps
module tb_sv39;
    reg clk=0, rst_n=0, request=0, user_mode, sum, mxr, pte_valid=0, pte_access_fault;
    reg [63:0] virtual_address, pte;
    reg [43:0] root_ppn;
    reg [1:0] access_kind;
    wire ready, pte_request, done;
    wire [55:0] pte_address, physical_address;
    wire [1:0] fault;
    sv39_walker dut (.*);
    integer fd, count, i, responses, j;
    reg [55:0] held_address;
    always #5 clk = !clk;
    initial begin
        fd = $fopen("vectors.txt", "r");
        if (!fd || $fscanf(fd, "%d", count) != 1)
            $fatal(1, "missing walker vectors");
        repeat (2) @(negedge clk);
        rst_n = 1;
        for (i = 0; i < count; i = i + 1) begin
            if (ready !== 1 || $fscanf(fd, "%h %h %h %b %b %b %d", virtual_address,
                     root_ppn, access_kind, user_mode, sum, mxr, responses) != 7)
                $fatal(1, "invalid walker vector");
            request = 1;
            @(negedge clk);
            request = 0;
            // A second request during a stalled walk must not overwrite the original context.
            virtual_address = ~virtual_address;
            root_ppn = ~root_ppn;
            access_kind = 3;
            for (j = 0; j < responses; j = j + 1) begin
                if ({ready, pte_request, done} !== 3'b010)
                    $fatal(1, "walker read handshake");
                held_address = pte_address;
                request = 1;
                @(negedge clk);
                request = 0;
                if ({ready, pte_request, done, pte_address} !== {3'b010, held_address})
                    $fatal(1, "walker advanced without a response");
                $display("P %014h", pte_address);
                if ($fscanf(fd, "%h %b", pte, pte_access_fault) != 2)
                    $fatal(1, "invalid PTE response");
                pte_valid = 1;
                @(negedge clk);
                pte_valid = 0;
            end
            if ({ready, pte_request, done} !== 3'b101)
                $fatal(1, "walker completion handshake");
            $display("D %0d %014h", fault, physical_address);
            @(negedge clk);
            if (done !== 0)
                $fatal(1, "walker completion must be a pulse");
        end
        $fclose(fd);
        $finish(0);
    end
endmodule
