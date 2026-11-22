module stage #(parameter N = 1)(input logic clk, a, b);
    for (genvar i = 0; i < 2; i++) begin: checks
        response: assert property (@(posedge clk) a |-> ##(N+i) b);
    end
endmodule
module top(input logic clk, a, b);
    stage #(.N(1)) short_delay(.*);
    stage #(.N(2)) long_delay(.*);
endmodule
