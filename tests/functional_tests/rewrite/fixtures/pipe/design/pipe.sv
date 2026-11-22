// A register stage, instantiated twice.
module pipe #(parameter int W = 4) (
    input  logic         clk,
    input  logic [W-1:0] d,
    output logic [W-1:0] q
);
    logic [W-1:0] r = '0;
    always_ff @(posedge clk) r <= d;
    assign q = r;

    // A design-level assertion: not translated, reported as a warning.
    a_design: assert property (@(posedge clk) q == r);
endmodule

module top (
    input  logic       clk,
    input  logic [3:0] d0,
    input  logic [3:0] d1,
    output logic [3:0] q0,
    output logic [3:0] q1
);
    pipe u0 (.clk(clk), .d(d0), .q(q0));
    pipe u1 (.clk(clk), .d(d1), .q(q1));
endmodule
