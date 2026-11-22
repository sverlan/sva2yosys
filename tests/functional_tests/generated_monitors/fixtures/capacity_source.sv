module tc(input logic clk, a, b);
  p: @KIND@ property (@(posedge clk) a |-> ##@N@ b);
endmodule
