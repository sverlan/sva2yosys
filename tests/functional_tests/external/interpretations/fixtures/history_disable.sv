module top;
  logic clk = 1'b0;
  int tick = 0;
  logic a = 1'b0;
  logic r = 1'b0;

  always #5 clk = ~clk;
  always @(posedge clk) tick <= tick + 1;

  initial begin
    #10 a = 1'b1;
    r = 1'b1;
    #10 r = 1'b0;
    #40;
    $display("SVA2YOSYS_PROBE done 1");
    $finish;
  end

  assert property (@(posedge clk) disable iff (r) (tick == 2) |-> $past(a))
    else $display("SVA2YOSYS_PROBE history_lost %0d", $sampled(tick));
  assert property (@(posedge clk) disable iff (r) (tick == 2) |-> !$past(a))
    else $display("SVA2YOSYS_PROBE history_present %0d", $sampled(tick));
endmodule
