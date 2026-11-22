// Hand-written reference monitor for:  disable iff (dis) a |-> ##[1:3] b   @(posedge clk)
// Canonical per-trigger-time slots: pend[j] = obligation triggered j+1 samples ago is still open.
// Timing contract: fail is combinational from (registered state, current samples) and is
// checked in the same step as the sample that makes the obligation impossible.
module monitor_window (
    input logic clk,
    input logic dis,
    input logic a,
    input logic b,
    output logic fail
);
    logic [2:0] pend = 3'b000;
    // Obligation of age 3 must see b now; ages 1..2 are discharged by b or move on.
    assign fail = !dis && pend[2] && !b;
    always @(posedge clk or posedge dis) begin
        if (dis) pend <= 3'b000;
        else     pend <= {pend[1] && !b, pend[0] && !b, a};
    end
    always @* if (!dis) assert (!fail);
    // Non-vacuous success of an age-3 obligation (b arrives on the last allowed sample).
    always @* if (!dis) cover (pend[2] && b);
endmodule
