module tb;
    logic clk=0, reset_n=0, in_valid=0, out_ready=0;
    logic [15:0] in_word=0;
    wire in_ready, out_valid;
    wire [15:0] out_word;
    fifo_two dut(clk,reset_n,in_valid,out_ready,in_word,in_ready,out_valid,out_word);

    // Drive while the clock is low, then sample settled ports on both sides of the edge.
    task tick(input int trial, step, input bit run, send, receive, input logic [15:0] word);
        logic pre_ready, pre_valid;
        logic [15:0] pre_word;
        reset_n=run; in_valid=send; out_ready=receive; in_word=word;
        #2;
        pre_ready=in_ready; pre_valid=out_valid; pre_word=out_word;
        clk=1;
        #2;
        $display("OBS SV-FIFO-RESET-PRIORITY %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d %0d",
                 trial,step,reset_n,in_valid,out_ready,in_word,
                 pre_ready,pre_valid,pre_word,in_ready,out_valid,out_word);
        clk=0;
        #2;
    endtask

    initial begin
        int trial, step;
        logic [15:0] tag;
        #2; clk=1;
        #2; clk=0;
        #2;
        trial=0;
        for (int occupied=0; occupied<=2; occupied++)
            for (int demand=0; demand<4; demand++)
                for (int reset=0; reset<2; reset++) begin
                    tag=16'(4096+16*trial);
                    step=0;
                    for (int next_word=1; next_word<=occupied; next_word++)
                        tick(trial,step++,1,1,0,tag+16'(next_word));
                    tick(trial,step++,reset==0,demand[1],demand[0],tag+16'd3);
                    repeat (2) tick(trial,step++,1,0,1,0);
                    tick(trial,step++,1,1,0,tag+16'd4);
                    tick(trial,step++,1,1,0,tag+16'd5);
                    repeat (3) tick(trial,step++,1,0,1,0);
                    trial++;
                end
        $display("DONE SV-FIFO-RESET-PRIORITY"); $finish;
    end
endmodule
