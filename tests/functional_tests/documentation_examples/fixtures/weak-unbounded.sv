module top(input logic clk, a, b, c);
    no_deadline: assert property (@(posedge clk) a |-> ##[1:$] b);
    repeated: assert property (@(posedge clk) a |-> b[*1:$] ##1 c);
    goto: assume property (@(posedge clk) a |=> b[->2]);
    nonconsecutive: assert property (@(posedge clk) a |-> b[=2] ##1 c);
    until_exclusive: assert property (@(posedge clk) a until b);
    until_inclusive: assume property (@(posedge clk) a until_with b);
endmodule
