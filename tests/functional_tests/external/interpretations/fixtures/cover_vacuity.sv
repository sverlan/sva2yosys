module top;
  logic clk = 0;
  int tick = 0;
  always #5 clk = !clk;
  always @(posedge clk) tick <= tick + 1;
  initial begin
    #60;
    $display("SVA2YOSYS_PROBE done 1");
    $finish;
  end
  logic a = 0;
  logic b = 1;
  initial #20 a = 1;
  cover property (@(posedge clk) a |-> b)
    $display("SVA2YOSYS_PROBE implication_match %0d", $sampled(tick));
  cover property (@(posedge clk) a && b)
    $display("SVA2YOSYS_PROBE boolean_match %0d", $sampled(tick));
  cover property (@(posedge clk) a && !b)
    $display("SVA2YOSYS_PROBE impossible_match %0d", $sampled(tick));
endmodule
