// Interface: enabled two-bit modulo-four counter with synchronous reset.
module counter(input logic clk, rst, enable, output logic [1:0] value);
    logic [1:0] count_q = 2'd0;

    always_ff @(posedge clk) begin
        if (rst)
            count_q <= 2'd0;
        else if (enable)
`ifdef EXAMPLE_BUG
            count_q <= count_q + 2'd2;
`else
            count_q <= count_q + 2'd1;
`endif
    end

    assign value = count_q;
endmodule
