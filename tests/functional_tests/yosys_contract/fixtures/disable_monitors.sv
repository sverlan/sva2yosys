// Window monitor for  disable iff (dis) a |-> ##[1:3] b  with a synchronous and an
// asynchronous clear of its slot state. Checked at the sampling edge (multiclock on).

module mon_sync (input logic clk, dis, a, b, output logic fail);
    logic [2:0] pend = 3'b000;
    assign fail = !dis && pend[2] && !b;
    always @(posedge clk)
        if (dis) pend <= 3'b000;
        else     pend <= {pend[1] && !b, pend[0] && !b, a};
    always @(posedge clk) if (!dis) assert (!fail);
endmodule

module mon_async (input logic clk, dis, a, b, output logic fail);
    logic [2:0] pend = 3'b000;
    assign fail = !dis && pend[2] && !b;
    always @(posedge clk or posedge dis)
        if (dis) pend <= 3'b000;
        else     pend <= {pend[1] && !b, pend[0] && !b, a};
    always @(posedge clk) if (!dis) assert (!fail);
endmodule
