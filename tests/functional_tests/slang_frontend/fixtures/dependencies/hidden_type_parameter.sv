// Review counterexample: the outer type parameter T gives v its width W; the block's own
// integer parameter T hides it at the assertion, so T (and with it W) is hidden.
module m #(parameter int W = 2, parameter type T = logic [W-1:0]) (input logic clk, a, b);
  T v;
  if (1) begin : g
    localparam int T = 1;
    h2: assert property (@(posedge clk) a |-> ##($bits(v)) b);
  end
endmodule
