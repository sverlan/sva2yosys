// Expression kinds, iff clocks, generate and instance arrays, user calls,
// and default disable declarations that span lines and share one file.
module expressions #(parameter int K = 3) (
    input logic       clk,
    input logic       en,
    input logic       a, b, c,
    input logic [3:0] v
);
    function automatic logic f(logic x);
        return !x;
    endfunction

    e_select:  assert property (@(posedge clk) v[1] |-> v[3:2] == 2'b10);
    e_concat:  assert property (@(posedge clk) {a, b} != 2'b00 || {2{c}} == 2'b11);
    e_cond:    assert property (@(posedge clk) (a ? b : c) |-> ##1 v[K]);
    e_iff:     assert property (@(posedge clk iff en) a |=> b);
    e_const:   assert property (@(posedge clk) a |-> (K == 3));
    e_ucall:   assert property (@(posedge clk) f(a) |-> b);

    for (genvar i = 0; i < 2; i++) begin : g
        g_bit: assert property (@(posedge clk) v[i] |-> ##1 v[i+2]);
    end
endmodule

module leaf (input logic clk, input logic rst, input logic x, input logic y);
    default clocking @(posedge clk); endclocking
    default disable iff
        (rst ||
         !x);
    l_assert: assert property (x |=> y);
endmodule

module second (input logic clk, input logic r2, input logic p);
    default clocking @(posedge clk); endclocking
    default disable iff (r2);
    s_assert: assert property (p |-> p);
endmodule

module expr_top (input logic clk, en, a, b, c, rst, r2, input logic [3:0] v);
    expressions u_expr (.*);
    leaf u_leaf [1:0] (.clk(clk), .rst(rst), .x(a), .y(b));
    second u_second (.clk(clk), .r2(r2), .p(c));
endmodule
