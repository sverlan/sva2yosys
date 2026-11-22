// Two instances of a module, the target of the bind fixtures.
module dut #(parameter int D = 2) (input logic clk, input logic a, output logic b);
    logic [D-1:0] sh = '0;
    always @(posedge clk) sh <= {sh, a};
    assign b = sh[D-1];
endmodule

module top (input logic clk, input logic a);
    logic b2, b3;
    dut #(.D(2)) u2 (.clk, .a, .b(b2));
    dut #(.D(3)) u3 (.clk, .a, .b(b3));
endmodule
