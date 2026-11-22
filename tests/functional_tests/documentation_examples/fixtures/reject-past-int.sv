module top(input logic clk, input int count);
    frontend_default: assert property (@(posedge clk) $past(count) > 0);
endmodule
