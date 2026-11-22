module top(input logic clk, a, b, input logic [3:0] v);
    past_one: assert property (@(posedge clk) $past(a) |-> b);
    past_two: assert property (@(posedge clk) $past(a, 2) |-> b);
    rising: assert property (@(posedge clk) $rose(a) |=> b);
    other_edges: assert property (@(posedge clk)
        $stable(v) || $changed(a) || $fell(b));
endmodule
