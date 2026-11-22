module tb(input clk, rst, a, b);
  wire fail, success;
  mon #(.CHECK(0)) m(.clk(clk), .i0(rst), .i1(a), .i2(b), .fail(fail), .success(success));
  // a[*1:N] can end now iff a is high: its length-one match suffices.
  // The Boolean consequent either succeeds or fails immediately, with no slots.
  always @* begin
    assert(fail == (!rst && a && !b));
    assert(success == (!rst && a && b));
  end
endmodule
