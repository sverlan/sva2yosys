module top(input logic clk, a, b, c);
    eventual_match: cover property (@(posedge clk) a ##[1:$] b);
    repeated: cover property (@(posedge clk) a[*2:$] ##1 b);
    goto: cover property (@(posedge clk) b[->2]);
    nonconsecutive: cover property (@(posedge clk) a |-> b[=2] ##1 c);
endmodule
