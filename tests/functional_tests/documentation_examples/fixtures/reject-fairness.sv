module top(input logic clk, a, b);
    several_configurations: assume property (@(posedge clk) a |-> strong(##[1:$] b));
endmodule
