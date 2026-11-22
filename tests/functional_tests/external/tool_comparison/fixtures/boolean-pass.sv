module top(input logic clk, a);
  always @(posedge clk) checked: assert property (a || !a);
endmodule
