// The AXI checker case: a struct parameter that only decides whether assertions exist.
typedef struct { bit en; int n; } cfg_t;
module m #(parameter cfg_t cfg = '{1'b1, 4}) (input logic clk, a, b);
  if (cfg.en) begin : g
    a11a: assert property (@(posedge clk) a |-> b);
    a11b: assert property (@(posedge clk) a |-> ##(cfg.n) b);
  end
endmodule
