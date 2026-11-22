module top(input logic clk, a, b);
    eventually_b: assume property (@(posedge clk) s_eventually b);
    until_b: assume property (@(posedge clk) a s_until b);
    provisional: cover property (@(posedge clk) a ##1 b);
endmodule
