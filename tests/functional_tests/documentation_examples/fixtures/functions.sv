module top(input logic clk, input logic [3:0] v);
    population: assert property (@(posedge clk) $countones(v) <= 2);
    one: assert property (@(posedge clk) $onehot(v));
    zero_or_one: assume property (@(posedge clk) $onehot0(v));
endmodule
