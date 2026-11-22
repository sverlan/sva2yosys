// A signal's declared type brings the parameters of that type; V is never mentioned.
module m #(parameter type T = logic [7:0], parameter W = 8, parameter V = 2)
  (input logic clk, a, b);
  typedef logic [W-1:0] data_t;
  T s;
  data_t s2;
  a4a: assert property (@(posedge clk) s[0] |-> b);
  a4b: assert property (@(posedge clk) (s2 == '0) |-> b);
  a4c: assert property (@(posedge clk) a |-> b);
endmodule
