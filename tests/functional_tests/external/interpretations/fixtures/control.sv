module top;
  logic clk = 1'b0;
  int tick = 0;
  logic a = 1'b1;

  always #5 clk = ~clk;
  always @(posedge clk) tick <= tick + 1;

  initial begin
    #20 a = 1'b0;
    #10 a = 1'b1;
    #30;
    $display("SVA2YOSYS_PROBE done 1");
    $finish;
  end

  assert property (@(posedge clk) a)
    else $display("SVA2YOSYS_PROBE fail %0d", $sampled(tick));
  assert property (@(posedge clk) !a)
    else $display("SVA2YOSYS_PROBE inverse %0d", $sampled(tick));
  assert property (@(posedge clk) (a || !a))
    else $display("SVA2YOSYS_PROBE bad_control %0d", $sampled(tick));
endmodule
