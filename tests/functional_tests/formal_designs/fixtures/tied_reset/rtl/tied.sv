// Three identical stages (Slang analyses the drivers of only one of them); u2 ties one
// reset port to a constant, as OpenTitan ties req_chk_i to 1'b1.
module tied (
    input  logic clk,
    input  logic rst_ni,
    input  logic rst_ref_ni,
    input  logic d,
    output logic q0,
    output logic q1,
    output logic q2
);

    stage u0 (.clk(clk), .rst_src_ni(rst_ni), .rst_dst_ni(rst_ref_ni), .d(d), .q(q0));
    stage u1 (.clk(clk), .rst_src_ni(rst_ref_ni), .rst_dst_ni(rst_ni), .d(d), .q(q1));
    stage u2 (.clk(clk), .rst_src_ni(1'b1), .rst_dst_ni(rst_ni), .d(d), .q(q2));
endmodule
