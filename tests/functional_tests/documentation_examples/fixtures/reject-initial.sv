module top(input logic clk, en, a);
    initial if (en) check_once: assert property (@(posedge clk) a);
endmodule
