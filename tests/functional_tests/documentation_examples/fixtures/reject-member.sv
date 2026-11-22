module top(input logic clk);
    struct packed {logic a; logic b;} value;
    member: assert property (@(posedge clk) value.a);
endmodule
