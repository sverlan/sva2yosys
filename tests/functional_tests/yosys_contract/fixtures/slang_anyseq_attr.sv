// The attribute form of the same free value: accepted by read_slang (contract point 20).
module anyseq_attr (input clk, output o);
  (* anyseq *) wire pick;
  reg q = 1'b0;
  always @(posedge clk) q <= pick;
  assign o = q;
endmodule
