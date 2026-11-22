module top(input logic clk, a, b, c, d);
    negation: assert property (@(posedge clk) not (a ##1 b));
    conjunction: assert property (@(posedge clk) (a |-> b) and (c |=> d));
    disjunction: assert property (@(posedge clk) (a |-> b) or (c |=> d));
    selected: assert property (@(posedge clk) if (a) (b |-> c) else (c |=> d));
    nested: assert property (@(posedge clk) a |-> (b |=> c));
    nonvacuous: cover property (@(posedge clk) (a |-> b) and (c |=> d));
endmodule
