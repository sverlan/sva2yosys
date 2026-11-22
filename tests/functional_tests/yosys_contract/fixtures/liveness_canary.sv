// Liveness canary: the procedural s_eventually form on a Boolean monitor signal.
// Each module is a top for some task of liveness_canary.sby.

// A free-running counter wraps: s_eventually (cnt == 0) holds.
module live_true (input clk);
  reg [1:0] cnt = 2'd0;
  always @(posedge clk) cnt <= cnt + 2'd1;
  wire x = (cnt == 2'd0);
  always @(posedge clk) assert property (s_eventually x);
endmodule

// r latches to 1 forever: x never holds, so s_eventually x fails.
module live_false (input clk);
  reg r = 1'b0;
  reg ran = 1'b0;
  always @(posedge clk) begin
    r <= 1'b1;
    ran <= 1'b1;
  end
  wire x = ran & ~r;
  always @(posedge clk) assert property (s_eventually x);
endmodule

// A free input: s_eventually in is false (in may stay 0) ...
module fair_without (input clk, input in);
  always @(posedge clk) assert property (s_eventually in);
endmodule

// ... unless the same property is assumed: a fairness constraint.
module fair_with (input clk, input in);
  always @(posedge clk) assume property (s_eventually in);
  always @(posedge clk) assert property (s_eventually in);
endmodule

// Only an assumption, no assertion.
module fair_only (input clk, input in);
  always @(posedge clk) assume property (s_eventually in);
endmodule

// A false safety assertion (cnt reaches 3) next to a true liveness property.
module mix_safety_false (input clk);
  reg [1:0] cnt = 2'd0;
  always @(posedge clk) cnt <= cnt + 2'd1;
  wire x = (cnt == 2'd0);
  always @* assert (cnt != 2'd3);
  always @(posedge clk) assert property (cnt != 2'd3);
  always @(posedge clk) assert property (s_eventually x);
endmodule

// A false liveness property and a safety assertion that is false on every
// infinite trace (ran is 1 from the second sample on).
module mix_vacuous (input clk);
  reg r = 1'b0;
  reg ran = 1'b0;
  always @(posedge clk) begin
    r <= 1'b1;
    ran <= 1'b1;
  end
  wire x = ran & ~r;
  always @* assert (!ran);
  always @(posedge clk) assert property (s_eventually x);
endmodule

// Safety property (true for SAFE_OK = 1, false for 0) plus a FALSE liveness
// property (x is never true), the latter guarded by the define.
module guarded #(parameter SAFE_OK = 1) (input clk);
  reg [1:0] cnt = 2'd0;
  always @(posedge clk) cnt <= cnt + 2'd1;
  wire x = (cnt == 2'd0) & (cnt == 2'd1);
  always @* assert (SAFE_OK ? (cnt <= 2'd3) : (cnt != 2'd3));
`ifdef SVA2YOSYS_LIVE
  always @(posedge clk) assert property (s_eventually x);
`endif
endmodule

// The same without the guard.
module unguarded (input clk);
  reg [1:0] cnt = 2'd0;
  always @(posedge clk) cnt <= cnt + 2'd1;
  wire x = (cnt == 2'd0) & (cnt == 2'd1);
  always @* assert (cnt <= 2'd3);
  always @(posedge clk) assert property (s_eventually x);
endmodule

// The same with a FALSE safety assertion (cnt reaches 3), next to the false
// unguarded liveness check.
module unguarded_bad (input clk);
  reg [1:0] cnt = 2'd0;
  always @(posedge clk) cnt <= cnt + 2'd1;
  wire x = (cnt == 2'd0) & (cnt == 2'd1);
  always @* assert (cnt != 2'd3); // SAFETY_UNGUARDED_BAD
  always @(posedge clk) assert property (s_eventually x);
endmodule

// A false liveness property alone ...
module conc_control (input clk);
  reg ran = 1'b0;
  always @(posedge clk) ran <= 1'b1;
  wire x = 1'b0;
  always @(posedge clk) assert property (s_eventually x);
endmodule

// ... and with a procedural concurrent Boolean assertion (no immediate one)
// that is false on every infinite trace (ran is 1 from the second sample on).
module conc_vacuous (input clk);
  reg ran = 1'b0;
  always @(posedge clk) ran <= 1'b1;
  wire x = 1'b0;
  always @(posedge clk) assert property (!ran);
  always @(posedge clk) assert property (s_eventually x);
endmodule

// Fairness that is not an invariant: t alternates, so "t eventually" is
// satisfiable by infinite traces, but t is not always true. The assertion
// s_eventually f (f never true) fails on those fair traces. Misreading the
// assumption as "always t" would leave no trace (t is 0 at the first sample)
// and pass vacuously.
module fair_toggle (input clk);
  reg t = 1'b0;
  always @(posedge clk) t <= ~t;
  wire f = 1'b0;
  always @(posedge clk) assume property (s_eventually t);
  always @(posedge clk) assert property (s_eventually f);
endmodule
