// The genvar selects a bit; N is never mentioned.
module m #(parameter N = 4) (input logic clk, a, b, input logic [3:0] v);
  for (genvar i = 0; i < 4; i++) begin : g
    a9a: assert property (@(posedge clk) v[i] |-> b);
    a9b: assert property (@(posedge clk) a |-> b);
  end
endmodule
