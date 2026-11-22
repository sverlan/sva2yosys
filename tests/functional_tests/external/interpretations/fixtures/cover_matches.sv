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
  logic b = 1;
  initial #10 a = 0;
  cover property (@(posedge clk) a ##[1:2] b)
    $display("SVA2YOSYS_PROBE match %0d", $sampled(tick));
endmodule
