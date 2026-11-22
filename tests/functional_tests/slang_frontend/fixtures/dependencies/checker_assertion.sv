// An assertion inside a checker is unknown; an assertion of the module around it is not.
checker chk(logic x, logic y, event clk);
  a13: assert property (@(clk) x |-> ##1 y);
endchecker
module m #(parameter P = 2) (input logic clk, a, b);
  logic [P-1:0] v;
  chk c1(v[0], b, posedge clk);
  am: assert property (@(posedge clk) a |-> b);
endmodule
