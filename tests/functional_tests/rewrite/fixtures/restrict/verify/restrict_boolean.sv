// IEEE 1800-2017 16.14.4: formal scenario constraints, not simulation checks.
// Each finite constraint has a reachable witness and a failing unconstrained control.
module restrict_boolean(input logic clk, a);
  scenario: restrict property (@(posedge clk) a);
  checked: assert property (@(posedge clk) a);
  witness: cover property (@(posedge clk) a);
endmodule
