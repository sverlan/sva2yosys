module top(input logic clk, a);
    first_unknown: assert property (@(posedge clk) a || $past(a));
endmodule
