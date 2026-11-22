// Two supported assertions (one unlabelled) and an assume.
module accepted (input logic clk, rst_n, req, ack);
    handshake: assert property (@(posedge clk) disable iff (!rst_n) req |-> ##[1:3] ack);
    assert property (@(posedge clk) req |=> ack);
    no_both: assume property (@(posedge clk) !(req && ack));
endmodule
