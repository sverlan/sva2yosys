// IEEE 1800-2017 16.14.4: formal scenario constraints, not simulation checks.
// Each finite constraint has a reachable witness and a failing unconstrained control.
module restrict_initial(input logic clk, a);
  initial scenario: restrict property (@(posedge clk) a);
  initial checked: assert property (@(posedge clk) a);
  witness: cover property (@(posedge clk) a ##1 !a);
endmodule
