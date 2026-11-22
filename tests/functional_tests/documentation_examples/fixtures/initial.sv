module top(input logic clk, a, b);
    initial once: assert property (@(posedge clk) a |=> b);
endmodule
