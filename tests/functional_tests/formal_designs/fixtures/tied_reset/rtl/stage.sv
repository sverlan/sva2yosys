// A register stage cleared by a combined reset, as in OpenTitan prim_sync_reqack:
// effective_rst_n is a continuous function of two reset inputs.
module stage (
    input  logic clk,
    input  logic rst_src_ni,
    input  logic rst_dst_ni,
    input  logic d,
    output logic q
);

    wire effective_rst_n;
    assign effective_rst_n = rst_src_ni && rst_dst_ni;

    always_ff @(posedge clk) begin
        if (!effective_rst_n) q <= 1'b0;
        else q <= d;
    end
endmodule
