// Mutant: reset does not clear the last valid bit.
module pipe3 (
    input  logic       clk,
    input  logic       rst,
    input  logic       in_valid,
    input  logic [7:0] in_data,
    output logic       out_valid,
    output logic [7:0] out_data
);
    logic [2:0] v  = 3'd0;
    logic [7:0] d0 = 8'd0;
    logic [7:0] d1 = 8'd0;
    logic [7:0] d2 = 8'd0;

    always_ff @(posedge clk) begin
        if (rst) v <= {v[1], 2'd0};
        else v <= {v[1:0], in_valid};
        d0 <= in_data;
        d1 <= d0;
        d2 <= d1;
    end

    assign out_valid = v[2];
    assign out_data  = d2;
endmodule
