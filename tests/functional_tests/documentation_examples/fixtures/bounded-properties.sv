module top(input logic clk, a, b);
    next_weak: assert property (@(posedge clk) nexttime [2] (a |-> b));
    next_strong: assert property (@(posedge clk) s_nexttime a);
    all_weak: assert property (@(posedge clk) always [0:2] a);
    all_strong: assert property (@(posedge clk) s_always [1:2] a);
    some_weak: assert property (@(posedge clk) eventually [0:2] a);
    some_strong: assert property (@(posedge clk) s_eventually [1:2] (a |-> b));
endmodule
