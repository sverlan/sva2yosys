// One supported assertion and two unsupported ones.
module mixed (input logic clk, clk2, a, b);
`ifdef WITH_GOOD
    good: assert property (@(posedge clk) a |-> ##1 b);
`endif
    unbounded: assume property (@(posedge clk) a |-> strong(##[1:$] b));
    two_clocks: assert property (@(posedge clk) a |=> @(posedge clk2) b);
endmodule
