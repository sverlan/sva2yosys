// Review counterexample: D is a parameter that an instantiation can override, and the
// property uses it; the block's own D hides it at the assertion, so the visible parameters
// (the inner D = 1) cannot key the monitor on it: D is hidden, the directive is rejected.
module m #(parameter int D = 2) (input logic clk, a, b);
  property p; a |-> ##D b; endproperty
  if (1) begin : g
    localparam int D = 1;
    h1: assert property (@(posedge clk) p);
  end
endmodule
