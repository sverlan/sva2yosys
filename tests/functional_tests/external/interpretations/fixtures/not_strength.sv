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
  logic a = 1;
  logic b = 0;
  assert property (@(posedge clk) not (a ##[1:$] b))
    else $display("SVA2YOSYS_PROBE fail %0d", $sampled(tick));
endmodule
