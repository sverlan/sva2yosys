// Concurrent assertions in initial procedures (IEEE 1800-2017 16.14.6): each has ONE
// evaluation attempt, at the first tick of its clock (step 0). x counts 0, 1, 2, ... so the
// attempt sees x = 0 .. 9 at steps 0 .. 9 and the assertion holds; the same assertion at module
// level would also be attempted at step 1, where x = 10 at step 10, and would fail there.
module cnt_props (
    input logic       clk,
    input logic [4:0] x
);

    initial a_below_ten: assert property (@(posedge clk) always [0:9] x < 10);
    initial c_three: cover property (@(posedge clk) x == 0 ##3 x == 3);
endmodule

bind cnt cnt_props u_props (.*);
