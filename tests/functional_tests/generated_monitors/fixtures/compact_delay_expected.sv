module tb(input clk, rst, a, b);
  wire fail, success;
  mon #(.CHECK(0)) m(.clk(clk), .i0(rst), .i1(a), .i2(b), .fail(fail), .success(success));
  // Hand-derived: a trigger at t checks b exactly at t+2.
  reg [1:0] triggers = 0;
  always @(posedge clk or posedge rst)
    if (rst) triggers <= 0;
    else triggers <= {triggers[0], a};
  always @* begin
    assert(fail == (!rst && triggers[1] && !b));
    assert(success == (!rst && triggers[1] && b));
  end
endmodule
