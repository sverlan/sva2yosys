// Properties of the counter that depend on the preprocessing environment:
// glue logic from a relative include, and an assertion enabled by -D WITH_STEPS.
module counter_props (
    input logic       clk,
    input logic       rst,
    input logic       en,
    input logic [3:0] cnt
);
    `include "glue.svh"

`ifdef WITH_STEPS
    a_steps: assert property (@(posedge clk) disable iff (rst)
        counting |=> cnt == $past(cnt) + 4'd1);
`endif
endmodule

bind counter counter_props u_props (.clk(clk), .rst(rst), .en(en), .cnt(cnt));
