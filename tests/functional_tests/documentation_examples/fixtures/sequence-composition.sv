module top(input logic clk, a, b, c);
    alternatives: assert property (@(posedge clk) (a ##1 b) or c);
    both: assert property (@(posedge clk) (a ##1 b) and (a ##2 c));
    same_end: assert property (@(posedge clk) (a ##1 b) intersect (b ##1 c));
    contained: cover property (@(posedge clk) b within (a ##2 c));
    invariant: assert property (@(posedge clk) a throughout (b ##1 c));
endmodule
