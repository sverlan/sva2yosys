// A register read before its declaration, as a generated monitor reads its sampled-value
// history registers (contract point 20).
module use_before (input clk, a, output o);
  assign o = h;
  logic h;
  always @(posedge clk) h <= a;
endmodule
