// $anyseq and the liveness witness pattern (CONTRACT.md points 17-19).
// Each module is a top for some task of liveness_canary.sby.

// --- $anyseq ---------------------------------------------------------------

// pick is free: "pick is always 0" fails in a prove task (pick = 1 at step 0).
module anyseq_safety (input clk);
  wire pick = $anyseq;
  always @* assert (!pick);
endmodule

// pick is free at every step: s_eventually !pick fails (pick = 1 forever).
module anyseq_live (input clk);
  wire pick = $anyseq;
  always @(posedge clk) assert property (s_eventually !pick);
endmodule

// The $anyseq wire and the liveness check exist only under the define. Without
// it only the true safety assertion remains (prove: PASS). With it,
// s_eventually pick fails (pick = 0 forever).
module guarded_anyseq (input clk);
  reg [1:0] cnt = 2'd0;
  always @(posedge clk) cnt <= cnt + 2'd1;
  always @* assert (cnt <= 2'd3);
`ifdef SVA2YOSYS_LIVE
  wire pick = $anyseq;
  always @(posedge clk) assert property (s_eventually pick);
`endif
endmodule

// --- the witness -----------------------------------------------------------

// Follows at most one request. busy_d:
//   busy_q = 1: stays busy until ack (ends at the sample where ack holds, and
//               does not start a new request in that sample);
//   busy_q = 0: starts when req && pick && !ack (a request answered in its
//               own sample never makes it busy).
module wit (input clk, input req, input ack);
  reg busy_q = 1'b0;
  wire pick = $anyseq;
  wire busy_d = busy_q ? !ack : (req && pick && !ack);
  always @(posedge clk) busy_q <= busy_d;
`ifdef SVA2YOSYS_LIVE
  always @(posedge clk) assert property (s_eventually !busy_q);
`endif
endmodule

// Mutant: the witness may start a new request in the sample where it ends,
// and ignores a same-sample answer when starting.
module wit_repick (input clk, input req, input ack);
  reg busy_q = 1'b0;
  wire pick = $anyseq;
  wire busy_d = (busy_q && !ack) || ((!busy_q || ack) && req && pick);
  always @(posedge clk) busy_q <= busy_d;
`ifdef SVA2YOSYS_LIVE
  always @(posedge clk) assert property (s_eventually !busy_q);
`endif
endmodule

// --- looping test bench support ----------------------------------------------

// t runs 0, 1, .., N-1, K, K+1, .., N-1, K, ... (samples 0..N-1, loop to K).
module loop_t #(parameter N = 1, parameter K = 0) (input clk, output reg [7:0] t);
  initial t = 8'd0;
  always @(posedge clk) t <= (t == N - 1) ? K : t + 8'd1;
endmodule

// t0: req=1 ack=0   t1: req=0 ack=1   t2: req=0 ack=0   then back to t0.
// Every request is answered one sample later. A witness that follows it is
// busy only in the sample after the pick (t1) and free again at t2: PASS.
module tb_answered (input clk);
  wire [7:0] t;
  loop_t #(.N(3), .K(0)) lp (.clk(clk), .t(t));
  reg req, ack;
  always @* case (t)
    8'd0: {req, ack} = 2'b10;
    8'd1: {req, ack} = 2'b01;
    default: {req, ack} = 2'b00;
  endcase
  wit w (.clk(clk), .req(req), .ack(ack));
endmodule

// t0: req=0 ack=0   t1: req=1 ack=0   t2: req=0 ack=0, then t2 forever (K=2).
// The only request is never answered. The witness may pick it at t1 (pick =
// 1 there, independent choice): busy_q = 1 from t2 on, forever: FAIL.
module tb_unanswered (input clk);
  wire [7:0] t;
  loop_t #(.N(3), .K(2)) lp (.clk(clk), .t(t));
  reg req, ack;
  always @* case (t)
    8'd1: {req, ack} = 2'b10;
    default: {req, ack} = 2'b00;
  endcase
  wit w (.clk(clk), .req(req), .ack(ack));
endmodule

// req=1 ack=1 in every sample (N=1). Every request is answered in its own
// sample: busy_d = 0 always, busy_q is never 1: PASS.
module tb_same_sample (input clk);
  wire [7:0] t;
  loop_t #(.N(1), .K(0)) lp (.clk(clk), .t(t));
  wire req = 1'b1;
  wire ack = 1'b1;
  wit w (.clk(clk), .req(req), .ack(ack));
endmodule

// req=1 in every sample, ack = 0 at t0 and 1 at t1 (N=2, loop to 0).
// Correct witness: it can pick at t0 (busy_q = 1 at t1); at t1 ack holds, so
// it ends (busy_d = 0) without re-picking although req && pick may hold. busy_q
// is therefore 0 at every t0: s_eventually !busy_q holds: PASS.
// Mutant: re-picks at t1 (busy_d = 1), is busy at t0, stays busy at t0 (ack=0)
// and re-picks at the next t1 ..., busy forever for the choice "always pick": FAIL.
module tb_next_sample (input clk);
  wire [7:0] t;
  loop_t #(.N(2), .K(0)) lp (.clk(clk), .t(t));
  wire req = 1'b1;
  reg ack;
  always @* case (t)
    8'd1: ack = 1'b1;
    default: ack = 1'b0;
  endcase
  wit w (.clk(clk), .req(req), .ack(ack));
endmodule

module tb_next_sample_mutant (input clk);
  wire [7:0] t;
  loop_t #(.N(2), .K(0)) lp (.clk(clk), .t(t));
  wire req = 1'b1;
  reg ack;
  always @* case (t)
    8'd1: ack = 1'b1;
    default: ack = 1'b0;
  endcase
  wit_repick w (.clk(clk), .req(req), .ack(ack));
endmodule
