module top(input logic clk, a);
  always @(posedge clk) checked: assert property (s_eventually a);
endmodule
