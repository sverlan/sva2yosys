// W sizes a net that the assertion does not read.
module m #(parameter W = 4) (input logic clk, a, b);
  wire [W-1:0] x;
  assign x = {W{a}};
  a3: assert property (@(posedge clk) a |-> b);
endmodule
