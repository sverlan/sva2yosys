// A type parameter used only by a variable that the assertion does not read.
module m #(parameter type T = int, parameter W = 3) (input logic clk, a, b);
  T unread;
  a12: assert property (@(posedge clk) a |-> b);
endmodule
