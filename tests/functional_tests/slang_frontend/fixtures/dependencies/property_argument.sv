// An actual argument K is folded to a constant in the expanded property; J is never mentioned.
module m #(parameter K = 2, parameter J = 7) (input logic clk, a, b);
  property p(n, x);
    x |-> ##n b;
  endproperty
  a6a: assert property (@(posedge clk) p(K, a));
  a6b: assert property (@(posedge clk) p(1, a));
endmodule
