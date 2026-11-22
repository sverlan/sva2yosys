// An escaped parameter name cannot be found by a lookup by spelling: it is matched by name.
// IEEE 1800-2017 5.6.1: an escaped identifier starts with a backslash and ends at white space.
module m #(parameter int \P-x = 3, parameter int \W-1 = 4, parameter int U = 5)
  (input logic clk, a, b);
  logic [\W-1 :0] s;
  e1: assert property (@(posedge clk) a |-> ##(\P-x ) b);
  e2: assert property (@(posedge clk) s != 0);
  e3: assert property (@(posedge clk) a |-> b);
endmodule
