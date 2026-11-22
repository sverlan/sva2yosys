module top(input logic clk, input bit a);
    default_zero: assert property (@(posedge clk) $past(a));
endmodule
