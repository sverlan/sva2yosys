// Representative basic assertion forms.
module basic_forms (
    input logic clk,
    input logic rst_n,
    input logic a, b, c, d
);
    p_overlap:     assert property (@(posedge clk) a |-> b);
    p_nonoverlap:  assert property (@(posedge clk) a |=> b);
    p_delay1:      assert property (@(posedge clk) a |-> ##1 b);
    p_delay5:      assert property (@(posedge clk) a |-> ##5 b);
    p_window:      assert property (@(posedge clk) a |-> ##[1:5] b);
    p_chain:       assert property (@(posedge clk) a |-> b ##[1:3] c);
    p_chain2:      assert property (@(posedge clk) a |-> b ##[1:3] c ##1 d);
    p_rep3:        assert property (@(posedge clk) a |-> b[*3]);
    p_rep25:       assert property (@(posedge clk) a |-> b[*2:5]);
    p_rose:        assert property (@(posedge clk) $rose(a) |-> ##[1:3] b);
    p_disable:     assert property (@(posedge clk) disable iff (!rst_n) a |-> ##3 b);
    p_sampled:     assert property (@(posedge clk) $stable(a) || $changed(b) || $fell(c) || $past(d));
    p_fusion:      assert property (@(posedge clk) a ##0 b |-> ##[0:2] c[*0:2] ##1 d);
    a_assume:      assume property (@(posedge clk) !(a && b));
    c_cover:       cover property (@(posedge clk) a ##[1:2] b);
endmodule
