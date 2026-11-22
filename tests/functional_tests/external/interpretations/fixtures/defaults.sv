module top;
  logic clk = 1'b0;
  int tick = 0;
  bit b;
  logic initialized = 1'b1;

  always #5 clk = ~clk;
  always @(posedge clk) tick <= tick + 1;

  initial begin
    #60;
    $display("SVA2YOSYS_PROBE done 1");
    $finish;
  end

  assert property (@(posedge clk) $stable(b))
    begin
      if ($sampled(tick) == 0)
        $display("SVA2YOSYS_PROBE stable_zero %0d", $sampled(tick));
    end
    else $display("SVA2YOSYS_PROBE bad_stable %0d", $sampled(tick));
  assert property (@(posedge clk) $past(initialized))
    else $display("SVA2YOSYS_PROBE initialized_past %0d", $sampled(tick));
endmodule
