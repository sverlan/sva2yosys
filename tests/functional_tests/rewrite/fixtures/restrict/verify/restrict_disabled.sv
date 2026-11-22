// IEEE 1800-2017 16.14.4: formal scenario constraints, not simulation checks.
// Each finite constraint has a reachable witness and a failing unconstrained control.
module restrict_disabled(input logic clk, reset, a);
  scenario: restrict property (@(posedge clk) disable iff (reset) a);
  checked: assert property (@(posedge clk) reset || a);
  witness: cover property (@(posedge clk) reset && !a);
endmodule
