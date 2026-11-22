// A package function cannot depend on a module parameter; its own PP is not visible here.
package pk;
  parameter PP = 3;
  function automatic bit g(bit x);
    return x ^ (PP == 3);
  endfunction
endpackage
module m #(parameter Q = 1) (input logic clk, a, b);
  a8: assert property (@(posedge clk) pk::g(a) |-> b);
endmodule
