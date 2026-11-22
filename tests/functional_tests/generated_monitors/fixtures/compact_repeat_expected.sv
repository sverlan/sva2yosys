module tb(input clk, rst, a, b);
  wire fail, success;
  mon #(.CHECK(0)) m(.clk(clk), .i0(rst), .i1(a), .i2(b), .fail(fail), .success(success));
  // Hand-derived: b must hold at launch and at each of the next two samples.
  reg [1:0] alive = 0;
  always @(posedge clk or posedge rst)
    if (rst) alive <= 0;
    else alive <= {alive[0] && b, a && b};
  always @* begin
    assert(fail == (!rst && (a || alive[0] || alive[1]) && !b));
    assert(success == (!rst && alive[1] && b));
  end
endmodule
