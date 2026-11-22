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
  logic selector;
  logic a = 0;
  logic b = 0;
  initial #1 selector = 1;
  assert property (@(posedge clk) if ($past(selector)) a else b)
    else $display("SVA2YOSYS_PROBE branch_failure %0d", $sampled(tick));
endmodule
