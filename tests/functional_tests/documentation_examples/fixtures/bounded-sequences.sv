module top(input logic clk, a, b, c);
    delays: assert property (@(posedge clk) a |-> b ##0 c ##2 b);
    ranges: assert property (@(posedge clk) a |=> ##[0:2] b);
    repeats: assert property (@(posedge clk) a |-> b[*2:3] ##1 c);
    empty: assert property (@(posedge clk) a |-> b[*0:2] ##1 c);
endmodule
