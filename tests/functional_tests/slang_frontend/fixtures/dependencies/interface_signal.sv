// A signal reached through an interface instance: unknown.
interface bus_if #(parameter AW = 4);
  logic [AW-1:0] addr;
  logic v;
endinterface
module m #(parameter W = 4) (input logic clk, a, b);
  bus_if #(.AW(W)) bi();
  x3: assert property (@(posedge clk) bi.v |-> b);
endmodule
