// Properties of the pipeline. The expected data is kept by supporting logic.
module pipe3_props (
    input logic       clk,
    input logic       rst,
    input logic       in_valid,
    input logic [7:0] in_data,
    input logic       out_valid,
    input logic [7:0] out_data
);
    // Supporting code: in_data, three samples late.
    logic [7:0] e1, e2, e3;
    always_ff @(posedge clk) begin
        e1 <= in_data;
        e2 <= e1;
        e3 <= e2;
    end

    a_latency: assert property (@(posedge clk) disable iff (rst)
        in_valid |-> ##3 (out_valid && out_data == e3));
    a_no_spurious: assert property (@(posedge clk) disable iff (rst)
        !in_valid |-> ##3 !out_valid);
    // Not disabled by reset: it checks the reset itself.
    a_reset: assert property (@(posedge clk) rst |=> !out_valid);
    c_burst: cover property (@(posedge clk) disable iff (rst) out_valid [*3]);
endmodule

bind pipe3 pipe3_props u_props (.*);
