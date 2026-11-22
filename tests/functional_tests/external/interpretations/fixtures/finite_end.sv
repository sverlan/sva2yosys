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
  logic b = 0;
  assert property (@(posedge clk) strong(1'b1 ##[1:$] b))
    else $display("SVA2YOSYS_PROBE strong_failure %0d", $sampled(tick));
endmodule
