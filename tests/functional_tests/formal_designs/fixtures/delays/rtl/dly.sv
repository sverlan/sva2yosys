// A delay line of N samples on a 2-bit vector.
module dly #(parameter int N = 1) (
    input  logic       clk,
    input  logic [1:0] in,
    output logic [1:0] out
);
    // sr[2k+1:2k] is in, k + 1 samples ago.
    logic [2*N+1:0] sr = '0;
    always_ff @(posedge clk) sr <= {sr[2*N-1:0], in};
    assign out = sr[2*N-1:2*N-2];
endmodule
