module top(input logic clk, a, en);
    gated: assert property (@(posedge clk) $past(a, 1, en));
endmodule
