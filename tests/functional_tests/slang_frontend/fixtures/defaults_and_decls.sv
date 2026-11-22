// default clocking, default disable iff, named sequence/property with arguments.
module defaults_and_decls #(parameter int N = 3) (
    input logic clk,
    input logic rst_n,
    input logic req, ack
);
    default clocking cb @(posedge clk); endclocking
    default disable iff (!rst_n);

    sequence s_resp(x, int lo, int hi);
        ##[lo:hi] x;
    endsequence

    property p_handshake(r, g);
        r |-> s_resp(g, 1, N);
    endproperty

    p_named:  assert property (p_handshake(req, ack));
    p_inline: assert property (req |=> ack);
endmodule

module top (input logic clk, rst_n, req, ack);
    defaults_and_decls #(.N(3)) u3 (.*);
    defaults_and_decls #(.N(5)) u5 (.*);
endmodule
