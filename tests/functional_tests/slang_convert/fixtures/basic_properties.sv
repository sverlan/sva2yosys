// The basic properties of the specification, plus forms that exercise
// clock/disable resolution, named declarations and expression mapping.
module basic_properties (
    input logic       clk,
    input logic       rst_n,
    input logic       a, b, c, d,
    input logic [3:0] v,
    input logic [7:4] off,
    input bit   [0:3] asc,
    input logic signed [7:0] s
);
    // --- the eleven properties of the specification -----------------------------
    p01: assert property (@(posedge clk) a |-> b);
    p02: assert property (@(posedge clk) a |=> b);
    p03: assert property (@(posedge clk) a |-> ##1 b);
    p04: assert property (@(posedge clk) a |-> ##5 b);
    p05: assert property (@(posedge clk) a |-> ##[1:5] b);
    p06: assert property (@(posedge clk) a |-> b ##[1:3] c);
    p07: assert property (@(posedge clk) a |-> b ##[1:3] c ##1 d);
    p08: assert property (@(posedge clk) a |-> b[*3]);
    p09: assert property (@(posedge clk) a |-> b[*2:5]);
    p10: assert property (@(posedge clk) $rose(a) |-> ##[1:3] b);
    p11: assert property (@(posedge clk) disable iff (!rst_n) a |-> ##3 b);

    // --- other directives and forms ---------------------------------------------
    q_seq:    assert property (@(posedge clk) a ##0 b ##[0:2] c[*0:2]);
    q_assume: assume property (@(posedge clk) !(a && b));
    q_cover:  cover property (@(posedge clk) a ##[1:2] b);
    q_sample: assert property (@(posedge clk) $stable(v) || $changed(a) || $fell(b) || $past(c));

    // --- expression mapping -------------------------------------------------------
    x_select: assert property (@(posedge clk) v[2] && v[3:2] == 2'b10 && off[5] && asc[0]);
    x_signed: assert property (@(posedge clk) s < -8'sd3 && (s >>> 1) != s);
    x_mix:    assert property (@(posedge clk) (a ? {b, c} : {2{d}}) == 2'b10);
endmodule

// default clocking, default disable iff, named declarations, explicit override.
module basic_defaults (input logic clk, rst_n, rst2, req, ack);
    default clocking @(posedge clk); endclocking
    default disable iff (!rst_n);

    sequence s_resp(x);
        ##[1:3] x;
    endsequence

    property p_hs(r, g);
        r |-> s_resp(g);
    endproperty

    d_named:    assert property (p_hs(req, ack));
    d_override: assert property (disable iff (rst2) req |=> ack);
endmodule

module basic_top (input logic clk, rst_n, rst2, a, b, c, d, req, ack,
                input logic [3:0] v, input logic [7:4] off, input bit [0:3] asc,
                input logic signed [7:0] s);
    basic_properties u_props (.*);
    basic_defaults u_defaults (.*);
endmodule
