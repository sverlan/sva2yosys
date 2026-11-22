module top(input logic clk, a, b);
  always @(posedge clk) checked: assert property (a ##1 b);
endmodule
