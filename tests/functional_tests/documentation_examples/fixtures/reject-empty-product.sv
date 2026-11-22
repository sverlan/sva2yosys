module top(input logic clk, a, b);
    empty_product: assert property (@(posedge clk) a[*0:2] intersect b);
endmodule
