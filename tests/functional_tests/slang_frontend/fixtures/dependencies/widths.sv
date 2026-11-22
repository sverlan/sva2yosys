// The width of a read variable or port; U sizes a variable that is not read.
module m #(parameter W = 8, parameter U = 2) (input logic clk, a, b, input logic [W-1:0] pin);
  logic [W-1:0] v;
  logic [U-1:0] unused;
  a5a: assert property (@(posedge clk) v[0] |-> b);
  a5b: assert property (@(posedge clk) pin[0] |-> b);
  a5c: assert property (@(posedge clk) a |-> b);
endmodule
