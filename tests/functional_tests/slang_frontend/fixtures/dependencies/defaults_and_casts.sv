// Enum values, a sequence argument, the default disable iff, a size cast, $bits of a type
// parameter and the enclosing condition of a procedural assertion. X is never mentioned.
module m #(parameter D = 2, parameter W = 4, parameter E = 3, parameter H = 5, parameter X = 6,
           parameter type T = logic) (input logic clk, rst, a, b);
  typedef enum logic [W-1:0] {S0, S1 = D} st_t;
  st_t st;
  default clocking cb @(posedge clk); endclocking
  default disable iff (rst == (E == 1));
  sequence s(x); x ##D b; endsequence
  x1: assert property (st == S1 |-> s(a));
  x1b: assert property (@(posedge clk) disable iff (1'b0) (st == S1) |-> b);
  x2: assert property (@(posedge clk) ((W)'(a) == 1) |-> b);
  x4: assert property (@(posedge clk) $bits(T) > 0 |-> b);
  always @(posedge clk) if (H > 2) x5: assert property (a |-> b);
endmodule
