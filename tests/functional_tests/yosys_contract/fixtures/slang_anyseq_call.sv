// The $anyseq call of the liveness witness: unknown to read_slang (contract point 20).
module anyseq_call (input clk, output o);
  wire pick = $anyseq;
  reg q = 1'b0;
  always @(posedge clk) q <= pick;
  assign o = q;
endmodule
