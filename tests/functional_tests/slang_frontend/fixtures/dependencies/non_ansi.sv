// Non-ANSI ports, struct fields, unpacked dimensions, a let and a folded constant function.
module m #(parameter W = 4, parameter N = 3, parameter F = 2, parameter L = 1, parameter G = 5,
           parameter U = 7, parameter type T = logic) (clk, a, b, v, tp);
  input clk, a, b;
  input [W-1:0] v;
  input T tp;
  typedef struct packed { logic [N-1:0] f; logic g; } st_t;
  st_t s;
  logic [F-1:0] arr [L];
  let lt(x) = x > G;
  function automatic int dbl(int x); return x * U; endfunction
  localparam LF = dbl(2);
  x6: assert property (@(posedge clk) v[0] |-> b);
  x7: assert property (@(posedge clk) s.f[0] |-> b);
  x8: assert property (@(posedge clk) arr[0][0] |-> b);
  x9: assert property (@(posedge clk) lt(1) |-> b);
  x10: assert property (@(posedge clk) a |-> ##LF b);
  x11: assert property (@(posedge clk) tp |-> b);
endmodule
