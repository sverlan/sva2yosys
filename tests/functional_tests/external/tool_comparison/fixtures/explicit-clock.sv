module top(input logic clk, a);
  checked: assert property (@(posedge clk) a);
endmodule
