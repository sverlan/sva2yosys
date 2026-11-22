// Per-bit latency of a delay line; one monitor variant per (N, bit).
module dly_props #(parameter int N = 1) (
    input logic       clk,
    input logic [1:0] in,
    input logic [1:0] out
);
    for (genvar i = 0; i < 2; i++) begin : g_bit
        a_latency: assert property (@(posedge clk) in[i] |-> ##N out[i]);
    end
    c_out: cover property (@(posedge clk) out[0]);
endmodule

bind dly dly_props #(.N(N)) u_props (.clk(clk), .in(in), .out(out));
