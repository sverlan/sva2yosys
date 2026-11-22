package support_pkg;
  sequence response(logic req, ack);
    req ##1 ack;
  endsequence
  property promise(logic req, ack);
    req |-> ack;
  endproperty
endpackage

module support_boundaries(input logic clk, clk2, a, b, en);
  sequence local_seq;
    a ##1 b;
  endsequence
  r_restrict: restrict property (@(posedge clk) a);
  r_negedge: assert property (@(negedge clk) a);
  r_iff: assert property (@(posedge clk iff en) a);
  r_negedge_iff: assert property (@(negedge clk iff en) a);
  r_multiclock: assert property (@(posedge clk) a |=> @(posedge clk2) b);
  r_gated_past: assert property (@(posedge clk) $past(a, 2, en));
  r_clocked_past: assert property (@(posedge clk) $past(a, 2, , @(posedge clk)));
  r_nested: assert property (@(posedge clk) $rose($past(a)));
  r_triggered: assert property (@(posedge clk) local_seq.triggered);
  r_first_match: assert property (@(posedge clk) first_match(a ##1 b));
  r_package_seq: assert property (@(posedge clk) support_pkg::response(a, b));
  r_package_prop: assert property (@(posedge clk) support_pkg::promise(a, b));
endmodule
