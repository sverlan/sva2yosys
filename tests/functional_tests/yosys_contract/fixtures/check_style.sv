// Property a |-> b at @(posedge clk), checked in two styles:
//   comb: always @*            (evaluated at every formal step)
//   clkd: always @(posedge clk) (evaluated at the sampling edge)

module chk_comb (input logic clk, a, b);
    always @* assert (!(a && !b));
endmodule

module chk_clkd (input logic clk, a, b);
    always @(posedge clk) assert (!(a && !b));
endmodule

// multiclock on: a=1, b=0 only BETWEEN edges (global step 4); a=0 at every edge.
module tb_mc #(parameter bit CLKD = 1'b1) ();
    logic [4:0] t = 5'd0;
    always @($global_clock) t <= t + 5'd1;
    wire clk = t[1];               // posedges at t = 2, 6, 10, ...
    wire a = (t == 5'd4);
    wire b = 1'b0;
    generate
        if (CLKD) begin : g
            chk_clkd m (.clk, .a, .b);
        end else begin : g
            chk_comb m (.clk, .a, .b);
        end
    endgenerate
endmodule

// multiclock off: a=1, b=0 at the sample of cycle 3 -> the property is violated at step 3.
module tb_sc #(parameter bit CLKD = 1'b1) (input logic clk);
    logic [3:0] c = 4'd0;
    always @(posedge clk) c <= c + 4'd1;
    wire a = (c == 4'd3);
    wire b = 1'b0;
    generate
        if (CLKD) begin : g
            chk_clkd m (.clk, .a, .b);
        end else begin : g
            chk_comb m (.clk, .a, .b);
        end
    endgenerate
endmodule
