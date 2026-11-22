module top(input logic clk, input logic [3:0] v);
    mixed_width: assert property (@(posedge clk) v inside {1});
endmodule
