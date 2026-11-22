// Review counterexample: the property names the module-level D (= N), but at the
// assertion in block g the name D is the block's own D, so a dependency keyed by the name D would
// select the wrong parameter and N would be lost. D is reported as hidden there (the directive is
// rejected); outside the block the name is the module-level D and the closure stops at it.
module m #(parameter int N = 2) (input logic clk, a, b);
  localparam int D = N;
  property p; a |-> ##D b; endproperty
  if (1) begin : g
    localparam int D = 1;
    s1: assert property (@(posedge clk) p);
  end
  s2: assert property (@(posedge clk) p);
endmodule
