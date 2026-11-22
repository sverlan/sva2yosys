module tb(input logic clk, a, key);
  localparam D = @DELAY@;
  logic [D-1:0] data_pipe = '0;
  logic [D-1:0] key_pipe = '0;
  always @(posedge clk) begin
    data_pipe <= {data_pipe[D-2:0], a ^ key};
    key_pipe <= {key_pipe[D-2:0], key};
  end
  wire b = data_pipe[D-1] ^ key_pipe[D-1];
  mon m(.clk(clk), .i0(a), .i1(b), .fail(), .success());
endmodule
