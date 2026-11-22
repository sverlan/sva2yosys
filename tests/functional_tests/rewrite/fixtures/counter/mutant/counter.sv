// Mutant of the counter: it skips from 7 to 9.
module counter (
    input  logic       clk,
    input  logic       rst,
    input  logic       en,
    output logic [3:0] cnt
);
    logic [3:0] cnt_q = 4'd0;

    always_ff @(posedge clk) begin
        if (rst) cnt_q <= 4'd0;
        else if (en) cnt_q <= (cnt_q == 4'd7) ? 4'd9 : cnt_q + 4'd1;
    end

    assign cnt = cnt_q;
endmodule
