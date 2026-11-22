// Mutant: the counter starts at 1, so x is 10 at step 9.
module cnt (
    input logic clk
);

    logic [4:0] x = 5'd1;

    always_ff @(posedge clk) x <= x + 5'd1;
endmodule
