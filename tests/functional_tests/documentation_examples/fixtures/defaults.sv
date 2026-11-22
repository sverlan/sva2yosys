module top(input logic clk, rst_n, a, b);
    default clocking @(posedge clk); endclocking
    default disable iff (!rst_n);
    sequence response; ##[1:3] b; endsequence
    property handshake; a |-> response; endproperty
    check_response: assert property (handshake);
    reached: cover property (a ##1 b);
endmodule
