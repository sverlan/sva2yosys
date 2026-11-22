module top(input logic clk, input logic [3:0] a, b);
    variable_divisor: assert property (@(posedge clk) (a / b) == 4'd1);
endmodule
