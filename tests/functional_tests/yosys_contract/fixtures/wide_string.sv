// A wide constant read with a variable index (contract point 21): the string form of a trace of
// 33 samples, as the SVA IEEE corpus used to write it. The row has its only 1 at sample 5, so the
// assertion fails (SBY reports step 6). Read by read_verilog, the SMT form of the read nests about
// a thousand concat terms; the smtbmc engine with --unroll stops with RecursionError.
module wide_string(input clk);
  logic [5:0] tick = 0;
  always @(posedge clk) if (tick != 6'd33) tick <= tick + 1'b1;
  wire [31:0] row_idx = (tick < 6'd33) ? 32'(tick) : 32'd32;
  wire [519:0] ROW = ". . . . . 1 . . . . . . . . . . . . . . . . . . . . . . . . . . .";
  wire x = (ROW[8*(64-2*row_idx) +: 8] == "1");
  always @(posedge clk) assert (!x);
endmodule
