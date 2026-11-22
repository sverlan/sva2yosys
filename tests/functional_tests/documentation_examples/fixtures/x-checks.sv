module top(input logic clk, a, input logic [3:0] v);
    only_x: assert property (@(posedge clk) !$isunknown(v));
    mixed: assert property (@(posedge clk) a && !$isunknown(v));
endmodule
