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
  assert property (@(posedge clk) (tick == 0) |=> 1'b0)
    else $display("SVA2YOSYS_PROBE fail %0d", $sampled(tick));
  assert property (@(posedge clk) (tick == 0) |=> 1'b1)
    else $display("SVA2YOSYS_PROBE bad_true %0d", $sampled(tick));
endmodule
