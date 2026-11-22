module tc(input logic clk, rst, a, b, c, input logic signed [3:0] s);
  p: @KIND@ property (@(posedge clk) disable iff (rst) @PROPERTY@);
endmodule
