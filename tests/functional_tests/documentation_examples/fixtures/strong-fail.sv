module top(input logic clk);
    logic q = 1'b0;
    always_ff @(posedge clk) q <= 1'b0;
    eventual: assert property (@(posedge clk) s_eventually q);
endmodule
