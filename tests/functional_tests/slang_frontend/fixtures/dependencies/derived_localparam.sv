// The closure stops at the localparam LP (an integer parameter); P is not needed.
module m #(parameter P = 3, parameter R = 1) (input logic clk, a, b);
  localparam LP = P * 2;
  localparam LQ = LP + 1;
  logic [LP-1:0] w;
  a10a: assert property (@(posedge clk) a |-> ##LP b);
  a10b: assert property (@(posedge clk) w[0] |-> b);
  a10c: assert property (@(posedge clk) a |-> ##LQ b);
endmodule
