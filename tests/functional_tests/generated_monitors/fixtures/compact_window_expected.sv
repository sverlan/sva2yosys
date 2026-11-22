module tb(input clk, rst, a, b);
  wire fail, success;
  mon #(.CHECK(0)) m(.clk(clk), .i0(rst), .i1(a), .i2(b), .fail(fail), .success(success));
  // Hand-derived: b ends all eligible obligations; age zero starts waiting.
  // After a missed age-three deadline, younger obligations remain independent.
  reg [2:0] pending = 0;
  always @(posedge clk or posedge rst)
    if (rst) pending <= 0;
    else pending <= {pending[1] && !b, pending[0] && !b, a};
  always @* begin
    assert(fail == (!rst && pending[2] && !b));
    assert(success == (!rst && (|pending) && b));
  end
endmodule
