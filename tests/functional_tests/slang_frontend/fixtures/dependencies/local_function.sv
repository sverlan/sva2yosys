// A module-local function brings the parameters of its body; Q is never mentioned.
module m #(parameter P = 1, parameter Q = 9) (input logic clk, a, b);
  function automatic bit f(bit x);
    return x ^ (P == 1);
  endfunction
  a7: assert property (@(posedge clk) f(a) |-> b);
endmodule
