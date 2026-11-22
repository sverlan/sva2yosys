// Mutant: the stage is cleared by the reset of the previous sample (a registered copy
// without initial value), so q can stay 0 after the reset has ended.
module stage (
    input  logic clk,
    input  logic rst_src_ni,
    input  logic rst_dst_ni,
    input  logic d,
    output logic q
);

    wire effective_rst_n;
    assign effective_rst_n = rst_src_ni && rst_dst_ni;
    logic late_rst_n;

    always_ff @(posedge clk) begin
        late_rst_n <= effective_rst_n;
        if (!late_rst_n) q <= 1'b0;
        else q <= d;
    end
endmodule
