module top(input logic clk, en, a, b);
    always @(posedge clk) begin
        if (en) check_enabled: assert property (a |=> b);
    end
    always_ff @(posedge clk) begin
        case (en)
            1'b1: reached: cover property (a ##1 b);
            default: ;
        endcase
    end
endmodule
