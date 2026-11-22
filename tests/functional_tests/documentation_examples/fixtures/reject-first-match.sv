module top(input logic clk, a, b);
    first_only: assert property (@(posedge clk) first_match(a ##[1:2] b));
endmodule
