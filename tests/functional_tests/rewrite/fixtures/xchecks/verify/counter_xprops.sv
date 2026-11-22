// X-check directives next to a real assertion, bound into every counter instance.
// $isunknown is evaluated as 0: the first five directives check nothing and are removed.
module counter_xprops (
    input logic       clk,
    input logic       rst,
    input logic       en,
    input logic [3:0] cnt
);
    default clocking cb @(posedge clk);
    endclocking
    default disable iff (rst);

    a_known: assert property (!$isunknown(cnt));
    a_en_known: assert property (en |-> !$isunknown(cnt));
    m_known: assume property (!$isunknown(en));
    c_unknown: cover property ($isunknown(cnt));
    a_trigger: assert property ($isunknown(cnt) |-> en);

    // The X part of the consequent is dropped; the rest is checked.
    a_real: assert property (en && cnt != 4'd15 |=> cnt == $past(cnt) + 4'd1 && !$isunknown(cnt));
    c_max: cover property (cnt == 4'd15);
endmodule

bind counter counter_xprops u_x (
    .clk(clk), .rst(rst), .en(en), .cnt(cnt)
);
