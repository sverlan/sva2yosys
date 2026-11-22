// X-checks: $isunknown is evaluated as 0 (the model is two-state).
module xchecks (input logic clk, req, ack, input logic [3:0] data);
    a_known: assert property (@(posedge clk) !$isunknown(data));
    a_valid_known: assert property (@(posedge clk) req |-> !$isunknown(data));
    m_known: assume property (@(posedge clk) !$isunknown(req));
    c_unknown: cover property (@(posedge clk) $isunknown(data));
    a_trigger: assert property (@(posedge clk) $isunknown(data) |-> ack);
    a_mixed: assert property (@(posedge clk) req |-> ##[1:3] ack && !$isunknown(data));
    a_real: assert property (@(posedge clk) req |-> ack);
endmodule
