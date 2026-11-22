// Does not compile: undeclared identifier.
module broken (input logic clk);
    p: assert property (@(posedge clk) missing_signal);
endmodule
