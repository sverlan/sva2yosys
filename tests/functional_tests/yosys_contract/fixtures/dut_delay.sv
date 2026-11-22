// b follows a after DELAY cycles. DELAY in 1..3 satisfies a |-> ##[1:3] b; DELAY=4 must fail.
module dut_delay #(parameter int DELAY = 2) (
    input logic clk,
    input logic a,
    output logic b
);
    logic [DELAY-1:0] sh = '0;
    always @(posedge clk) sh <= {sh, a};
    assign b = sh[DELAY-1];
endmodule

module top_delay #(parameter int DELAY = 2) (input logic clk, input logic a, input logic dis);
    logic b, fail;
    dut_delay #(.DELAY(DELAY)) u_dut (.clk, .a, .b);
    monitor_window u_mon (.clk, .dis, .a, .b, .fail);
endmodule
