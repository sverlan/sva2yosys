module top(input logic clk, rst_n, a, b);
    logic registered_reset;
    always_ff @(posedge clk) registered_reset <= !rst_n;
    wrong_region: assert property (@(posedge clk) disable iff (registered_reset) a |=> b);
endmodule
