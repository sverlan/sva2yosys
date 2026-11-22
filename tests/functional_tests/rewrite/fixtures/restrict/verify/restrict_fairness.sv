// IEEE 1800-2017 16.14.4: formal scenario constraints, not simulation checks.
// Each finite constraint has a reachable witness and a failing unconstrained control.
module restrict_fairness(input logic clk, a, b);
  scenario: restrict property (@(posedge clk) a |-> s_eventually b);
  checked: assert property (@(posedge clk) a |-> s_eventually b);
endmodule
