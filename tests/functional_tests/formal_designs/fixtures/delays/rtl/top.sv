// Two delay lines with different lengths.
module top (
    input  logic       clk,
    input  logic [1:0] a,
    input  logic [1:0] b,
    output logic [1:0] y1,
    output logic [1:0] y3
);
    dly #(.N(1)) u1 (.clk(clk), .in(a), .out(y1));
    dly #(.N(3)) u3 (.clk(clk), .in(b), .out(y3));
endmodule
