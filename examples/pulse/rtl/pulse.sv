// Interface: registered rising-edge detector; output describes the previous tick.
module pulse(input logic clk, rst, level, output logic pulse_out);
    logic previous_level = 1'b0;
    logic pulse_q = 1'b0;

    always_ff @(posedge clk) begin
        previous_level <= level;  // follows the input, also during reset
        if (rst)
            pulse_q <= 1'b0;
`ifdef EXAMPLE_BUG
        else
            pulse_q <= level;
`else
        else
            pulse_q <= level && !previous_level;
`endif
    end

    assign pulse_out = pulse_q;
endmodule
