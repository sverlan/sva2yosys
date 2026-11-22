// N is a folded constant in the delay; M is never mentioned.
module m #(parameter N = 3, parameter M = 5) (input logic clk, a, b);
  a1: assert property (@(posedge clk) a |-> ##N b);
endmodule
