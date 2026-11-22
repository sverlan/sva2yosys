// Review counterexample: the closure stops at the localparam LP, but LP's explicit type
// [W-1:0] gives $bits(LP) its value, so W is a dependency: with W=3 the delay is 3, not 2.
module m #(parameter int W = 2) (input logic clk, a, b);
  localparam logic [W-1:0] LP = 1;
  e1: assert property (@(posedge clk) a |-> ##($bits(LP)) b);
  // A parameter with an implicit type has no type syntax to follow: only LP.
  localparam LQ = 1;
  e2: assert property (@(posedge clk) a |-> ##LQ b);
endmodule
