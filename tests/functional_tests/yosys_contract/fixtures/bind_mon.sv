// Checker module with one immediate assertion.
module mon (input logic clk, input logic a, input logic b);
    always @* assert (!(a && b));
endmodule
