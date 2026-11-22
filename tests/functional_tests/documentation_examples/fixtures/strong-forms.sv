module top(input logic clk, a, b);
    sequence_live: assert property (@(posedge clk) strong(a ##[1:$] b));
    eventual_live: assert property (@(posedge clk) s_eventually b);
    until_live: assert property (@(posedge clk) a s_until b);
    inclusive_live: assert property (@(posedge clk) a s_until_with b);
    negated_weak: assert property (@(posedge clk) not (a until b));
    negated_strong: assume property (@(posedge clk) not strong(a ##[1:$] b));
endmodule
