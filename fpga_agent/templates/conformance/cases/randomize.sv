class constrained_pair;
    rand bit [3:0] left, right;

    constraint legal_pair {
        left inside {[2:9]};
        right == left + 4'd2;
    }
endclass

module tb;
    task automatic observe(string scenario, int index, bit solved, constrained_pair pair);
        $display("OBS SV-RANDOMIZE %s %0d %0d %0d %0d",
                 scenario,index,solved,pair.left,pair.right);
    endtask

    initial begin
        constrained_pair pair=new;
        bit solved;
        for (int index=0; index<8; index++) begin
            solved=pair.randomize();
            observe("base",index,solved,pair);
        end
        solved=pair.randomize() with { left inside {4,8}; };
        observe("inline",0,solved,pair);

        pair.left=5;
        pair.right=15;
        pair.left.rand_mode(0);
        solved=pair.randomize();
        observe("frozen",0,solved,pair);

        pair.left=12;
        pair.right=15;
        solved=pair.randomize();
        observe("frozen_bad",0,solved,pair);

        pair.left.rand_mode(1);
        solved=pair.randomize() with { left == 3; };
        observe("thawed",0,solved,pair);

        pair.left=0;
        pair.right=15;
        solved=pair.randomize() with { left == 1; };
        observe("unsat",0,solved,pair);
        $display("DONE SV-RANDOMIZE");
        $finish;
    end
endmodule
