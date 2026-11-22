// Properties of the handshake, attached to every handshake instance by bind.
module handshake_props (
    input logic clk,
    input logic rst,
    input logic req,
    input logic ack
);
    default clocking @(posedge clk);
    endclocking
    default disable iff (rst);

    // A request stays up until it is acknowledged.
    a_hold: assert property (req && !ack |=> req);

    // Every request is acknowledged within one to three samples.
    a_resp: assert property ($rose(req) |-> ##[1:3] ack);

    // An acknowledge only answers a pending request.
    a_ack_with_req: assert property (ack |-> req);

    // A complete handshake happens.
    c_done: cover property ($rose(req) ##[1:3] ack);
endmodule

bind handshake handshake_props u_props (.clk(clk), .rst(rst), .req(req), .ack(ack));
