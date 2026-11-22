// The module-level clocked form of a liveness property: Yosys's Verilog frontend
// has no concurrent-assertion syntax outside procedural code.
module bad (input clk, input x);
  assert property (@(posedge clk) s_eventually x);
endmodule
