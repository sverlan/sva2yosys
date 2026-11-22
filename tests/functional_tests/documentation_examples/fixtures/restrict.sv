module top(input logic clk, a, b);
    scenario: restrict property (@(posedge clk) a |=> b);
    checked: assert property (@(posedge clk) a |=> b);
endmodule
