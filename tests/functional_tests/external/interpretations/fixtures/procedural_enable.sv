module top;
  logic clk = 1'b0;
  int tick = 0;
  logic a = 1'b0;
  logic stablegate = 1'b0;
  logic blocking_gate = 1'b0;

  always #5 clk = ~clk;
  always @(posedge clk) tick <= tick + 1;

  initial begin
    #20 stablegate = 1'b1;
    #20 stablegate = 1'b0;
    #20;
    $display("SVA2YOSYS_PROBE done 1");
    $finish;
  end

  always @(posedge clk) begin
    if (stablegate)
      assert property (a)
        else $display("SVA2YOSYS_PROBE stable_enable %0d", $sampled(tick));
  end

  always @(posedge clk) begin
    blocking_gate = 1'b1;
    if (blocking_gate)
      assert property (a)
        else $display("SVA2YOSYS_PROBE blocking_enable %0d", $sampled(tick));
  end

  always @(negedge clk) blocking_gate = 1'b0;
endmodule
