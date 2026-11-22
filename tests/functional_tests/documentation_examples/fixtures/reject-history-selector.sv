module top(input logic clk, a, b, c);
    unknown_branch: assert property (@(posedge clk) if ($past(a)) b else c);
endmodule
