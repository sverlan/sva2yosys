// Consequents that can never fail: a_live, m_live, a_prop, a_const, a_next, a_and warn;
// a_rep, a_until, a_tail, a_cover and a_bounded do not.
module never_fails (input logic clk, a, b, c, d);
    a_live: assert property (@(posedge clk) a |-> ##[1:$] b);
    m_live: assume property (@(posedge clk) a |-> ##[1:$] b ##1 c);
    a_prop: assert property (@(posedge clk) ##[1:$] b);
    a_const: assert property (@(posedge clk) a |-> ##[1:$] 1'b0);
    a_next: assert property (@(posedge clk) a |=> ##[1:$] b);
    a_and: assert property (@(posedge clk) (a |-> b) and (a |-> ##[1:$] c));
    a_rep: assert property (@(posedge clk) a |-> b[*1:$]);
    a_until: assert property (@(posedge clk) a |-> b until c);
    a_tail: assert property (@(posedge clk) a |-> b ##1 (c ##[1:$] d));
    c_cover: cover property (@(posedge clk) a ##[1:$] b);
    a_bounded: assert property (@(posedge clk) a |-> ##[1:3] b);
endmodule
