// EN only decides whether the assertion exists; N is never mentioned.
module m #(parameter EN = 1, parameter N = 2) (input logic clk, a, b);
  if (EN) begin : g
    a2: assert property (@(posedge clk) a |-> b);
  end
endmodule
