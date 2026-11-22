module top;
  logic clk = 1'b0;
  int tick = 0;
  logic a = 1'b1;
  logic rise_q = 1'b0;
  logic fall_q = 1'b1;

  always #5 clk = ~clk;
  always @(posedge clk) begin
    tick <= tick + 1;
    rise_q <= tick >= 2;
    fall_q <= tick < 2;
  end

  initial begin
    #10 a = 1'b0;
    #30 a = 1'b1;
    #20;
    $display("SVA2YOSYS_PROBE done 1");
    $finish;
  end

  assert property (@(posedge clk) disable iff (rise_q) a)
    else $display("SVA2YOSYS_PROBE rise %0d", $sampled(tick));
  assert property (@(posedge clk) disable iff (fall_q) a)
    else $display("SVA2YOSYS_PROBE fall %0d", $sampled(tick));
endmodule
