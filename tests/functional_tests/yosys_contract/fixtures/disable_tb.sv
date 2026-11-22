// Experiment: does a disable pulse that lies strictly between two sampling edges abort a live
// obligation of  disable iff (dis) a |-> ##[1:3] b ?  IEEE 1800 (16.12) says yes: disable iff is
// evaluated asynchronously on current values.  Run with `multiclock on`.
//
// Global steps t: clk = t[1] -> posedges at t = 2, 6, 10, 14.
// a = 1 for t <= 2   -> obligation triggered at edge t=2.
// b = 0 always       -> without disable the obligation fails at edge t=14 (age 3).
// dis = 1 only at t=4 (between edges 2 and 6, gone before edge 6 samples).

module tb_between_edges #(parameter bit ASYNC = 1'b1, parameter bit PULSE = 1'b1) ();
    logic [4:0] t = 5'd0;
    always @($global_clock) t <= t + 5'd1;
    wire clk = t[1];
    wire a   = (t <= 5'd2);
    wire b   = 1'b0;
    wire dis = PULSE && (t == 5'd4);
    logic fail;
    generate
        if (ASYNC) begin : g
            mon_async m (.clk, .dis, .a, .b, .fail);
        end else begin : g
            mon_sync m (.clk, .dis, .a, .b, .fail);
        end
    endgenerate
endmodule
