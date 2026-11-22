module top(input logic clk, a, b, c, d);
    unknown_nonvacuity: cover property (@(posedge clk) (a |-> b) or (c |-> d));
endmodule
