module top(input logic clk, input logic [3:0] v);
    uniform: assert property (@(posedge clk) v inside {4'd1, [4'd3:4'd7]});
    masked_item: cover property (@(posedge clk) v inside {4'b10??});
    open_bound: assert property (@(posedge clk) v inside {[$:4'd7]});
endmodule
