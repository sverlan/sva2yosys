module top(input logic clk, a, b, c);
    delay: assert property (@(posedge clk) a ##[1:$] b |-> c);
    repetition: assume property (@(posedge clk) a[*1:$] ##1 b |=> c);
    ongoing: assert property (@(posedge clk) always (a |-> ##2 b));
endmodule
