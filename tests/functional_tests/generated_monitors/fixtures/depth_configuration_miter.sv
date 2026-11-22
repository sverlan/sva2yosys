module tb(input clk, rst, a, b, c);
  wire before_fail, before_success, after_fail, after_success;
  baseline #(.CHECK(0)) before_monitor(.clk(clk), .i0(rst), .i1(a), .i2(b), .i3(c),
      .fail(before_fail), .success(before_success));
  mon #(.CHECK(0)) after_monitor(.clk(clk), .i0(rst), .i1(a), .i2(b), .i3(c),
      .fail(after_fail), .success(after_success));
  always @* begin
    assert(before_fail == after_fail);
    assert(before_success == after_success);
  end
endmodule
