module tb(input clk, @PORTS@);
  wire fail_d, success_d, fail_c, success_c;
  dense #(.CHECK(0)) d(.clk(clk), @CONNECTIONS@ .fail(fail_d), .success(success_d));
  mon #(.CHECK(0)) c(.clk(clk), @CONNECTIONS@ .fail(fail_c), .success(success_c));
  // No sticky failure mask: equality must hold after any earlier failures too.
  always @* assert(fail_d == fail_c && success_d == success_c);
endmodule
