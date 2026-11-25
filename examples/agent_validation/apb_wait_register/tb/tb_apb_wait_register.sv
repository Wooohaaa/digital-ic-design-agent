`timescale 1ns/1ps
module tb_apb_wait_register;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst_n = 0, psel = 0, penable = 0, pwrite = 0;
    reg [7:0] paddr = 0, pwdata = 0;
    wire pready, pslverr;
    wire [7:0] prdata;
    apb_wait_register dut(.*);

    // Check before the rising edge consumes the response.
    task automatic cycle(
        input reg release_reset, select, enable, write_access,
        input reg [7:0] addr, data,
        input reg ready, error,
        input reg [7:0] result
    );
        @(negedge clk);
        {rst_n, psel, penable, pwrite, paddr, pwdata} =
            {release_reset, select, enable, write_access, addr, data};
        @(posedge clk);
        if ({pready, pslverr, prdata} !== {ready, error, result})
            $fatal(1, "APB edge %0t: inputs=%h expected=%h got=%h", $time,
                   {rst_n, psel, penable, pwrite, paddr, pwdata},
                   {ready, error, result}, {pready, pslverr, prdata});
    endtask

    task automatic transfer(
        input reg write_access,
        input reg [7:0] addr, data,
        input reg error,
        input reg [7:0] result
    );
        cycle(1, 1, 0, write_access, addr, data, 0, 0, 0);
        cycle(1, 1, 1, write_access, addr, data, 0, 0, 0);
        cycle(1, 1, 1, write_access, addr, data, 1, error, result);
    endtask

    integer index;
    initial begin
        cycle(0, 0, 0, 0, 0, 0, 0, 0, 0);
        cycle(1, 1, 1, 1, 0, 8'hb0, 0, 0, 0); // Orphan access.
        transfer(0, 8'h04, 0, 0, 0);

        transfer(1, 8'h00, 8'h45, 0, 0);
        cycle(1, 1, 1, 1, 0, 8'h45, 0, 0, 0); // Access stays asserted after completion.
        cycle(1, 1, 1, 1, 0, 8'h45, 0, 0, 0);
        transfer(0, 8'h04, 0, 0, 1);
        transfer(0, 8'h00, 0, 0, 8'h45);

        transfer(1, 8'h04, 8'h91, 1, 0);
        transfer(1, 8'h01, 8'h92, 1, 0);
        transfer(0, 8'hfc, 0, 1, 0);
        transfer(0, 8'h04, 0, 0, 1);
        transfer(0, 8'h00, 0, 0, 8'h45);

        // A malformed master changes all request fields during access.
        cycle(1, 1, 0, 1, 0, 8'h78, 0, 0, 0);
        cycle(1, 1, 1, 0, 8'h04, 8'h12, 0, 0, 0);
        cycle(1, 1, 1, 0, 8'hfe, 8'h34, 1, 0, 0);
        transfer(0, 8'h04, 0, 0, 2);
        transfer(0, 8'h00, 0, 0, 8'h78);

        // Deselection at the would-be completion edge cancels the write.
        cycle(1, 1, 0, 1, 0, 8'h89, 0, 0, 0);
        cycle(1, 1, 1, 1, 0, 8'h89, 0, 0, 0);
        cycle(1, 0, 1, 1, 0, 8'h89, 0, 0, 0);
        cycle(1, 1, 1, 1, 0, 8'h89, 0, 0, 0);
        transfer(0, 8'h04, 0, 0, 2);
        transfer(0, 8'h00, 0, 0, 8'h78);

        // Synchronous reset wins over a pending write while access stays high.
        cycle(1, 1, 0, 1, 0, 8'hff, 0, 0, 0);
        cycle(1, 1, 1, 1, 0, 8'hff, 0, 0, 0);
        cycle(0, 1, 1, 1, 0, 8'hff, 0, 0, 0);
        cycle(1, 1, 1, 1, 0, 8'hff, 0, 0, 0);
        transfer(0, 8'h04, 0, 0, 0);
        transfer(0, 8'h00, 0, 0, 0);

        // Keep PSEL high between each setup/access sequence and through wrap.
        for (index = 0; index < 256; index = index + 1)
            transfer(1, 8'h00, index[7:0], 0, 0);
        transfer(0, 8'h04, 0, 0, 0);
        transfer(0, 8'h00, 0, 0, 8'hff);
        transfer(1, 8'h00, 8'h11, 0, 0);
        transfer(0, 8'h04, 0, 0, 1);
        $display("PASS: APB wait, consecutive transfers, single commit, error, cancellation and reset");
        $finish;
    end

    initial begin
        #15000;
        $fatal(1, "APB wait-state test timed out");
    end
endmodule
