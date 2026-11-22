// The stage forwards d unless the combined reset is active; an attempt whose start sample
// sees the reset is disabled (the reset clears q at the next sample).
module stage_props (
    input logic clk,
    input logic effective_rst_n,
    input logic d,
    input logic q
);

    a_follow: assert property (@(posedge clk) disable iff (!effective_rst_n) d |=> q);
    c_follow: cover property (@(posedge clk) disable iff (!effective_rst_n) d ##1 q);
endmodule

bind stage stage_props u_props (.*);
