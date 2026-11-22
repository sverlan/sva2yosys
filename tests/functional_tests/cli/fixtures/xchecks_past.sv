// $isunknown of a $past operand: X at the first samples, not representable (rejected).
module xchecks_past (input logic clk, req, input logic [3:0] data);
    a_past: assert property (@(posedge clk) req |-> !$isunknown($past(data)));
    a_ok: assert property (@(posedge clk) req |-> !$isunknown(data));
endmodule
