// A compilation-unit parameter cannot be overridden by an instantiation, but it is not among the
// visible names: the result is unknown even when a block's own D hides it (it must not become
// the inner D).
parameter int D = 2;
module m (input logic clk, a, b);
  property p; a |-> ##D b; endproperty
  if (1) begin : g
    localparam int D = 1;
    u1: assert property (@(posedge clk) p);
  end
endmodule
