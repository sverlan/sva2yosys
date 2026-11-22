// A free-running counter that starts at 0 (a declaration initializer). It counts 0, 1, 2, ...
// and is five bits wide, so it does not wrap within the ten steps the properties look at.
module cnt (
    input logic clk
);

    logic [4:0] x = 5'd0;

    always_ff @(posedge clk) x <= x + 5'd1;
endmodule
