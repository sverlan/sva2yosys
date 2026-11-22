// Per-bit properties of a pipe stage; one generate iteration per bit.
module pipe_props #(parameter int W = 4) (
    input logic         clk,
    input logic [W-1:0] d,
    input logic [W-1:0] q
);
    for (genvar i = 0; i < W; i++) begin : g_bit
        logic din, dout;
        assign din = d[i];
        assign dout = q[i];
        a_bit: assert property (@(posedge clk) din |=> dout);
    end
endmodule

bind pipe pipe_props #(.W(4)) u_props (.clk(clk), .d(d), .q(q));
