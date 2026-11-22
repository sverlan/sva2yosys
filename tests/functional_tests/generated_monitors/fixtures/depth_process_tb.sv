module tb(input clk, probe, @PORTS@);
  wire fail, success;
  mon #(.CHECK(0)) m(.clk(clk), @CONNECTIONS@ .fail(fail), .success(success));
  // Deliberately false for probe=0. Both monitor outputs remain in the cone.
  // FAIL, rather than ERROR, verifies SBY preparation and engine handoff.
  always @* assert(probe && (fail == success));
endmodule
