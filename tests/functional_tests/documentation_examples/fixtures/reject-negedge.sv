module top(input logic clk, a);
    falling_clock: assert property (@(negedge clk) a);
endmodule
