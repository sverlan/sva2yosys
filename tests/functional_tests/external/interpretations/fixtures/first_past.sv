module top;
  logic clk = 1'b0;
  int tick = 0;
  logic a;

  always #5 clk = ~clk;
  always @(posedge clk) tick <= tick + 1;

  initial begin
    #1 a = 1'b1;
    #59;
    $display("SVA2YOSYS_PROBE done 1");
    $finish;
  end

  assert property (@(posedge clk) $past(a))
    else $display("SVA2YOSYS_PROBE first_past %0d", $sampled(tick));
  assert property (@(posedge clk) (tick == 1) |-> !$past(a))
    else $display("SVA2YOSYS_PROBE history_present %0d", $sampled(tick));
endmodule
