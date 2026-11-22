// Mutant: one sample too late.
module dly #(parameter int N = 1) (
    input  logic       clk,
    input  logic [1:0] in,
    output logic [1:0] out
);
    logic [2*N+1:0] sr = '0;
    always_ff @(posedge clk) sr <= {sr[2*N-1:0], in};
    assign out = sr[2*N+1:2*N];
endmodule
