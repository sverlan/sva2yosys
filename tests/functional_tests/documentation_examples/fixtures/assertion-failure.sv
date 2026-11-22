module top(input logic clk);
    logic q = 1'b0;
    always_ff @(posedge clk) q <= 1'b1;
    fails_initially: assert property (@(posedge clk) q);
endmodule
