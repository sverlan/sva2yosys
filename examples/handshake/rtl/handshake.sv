// A request/acknowledge handshake.
// start (when idle) raises req; ack is given two samples after req rises,
// and req drops the sample after ack.
module handshake (
    input  logic clk,
    input  logic rst,
    input  logic start,
    output logic req,
    output logic ack
);
    logic       req_q = 1'b0;
    logic       ack_q = 1'b0;
    logic [1:0] cnt   = 2'd0;

    always_ff @(posedge clk) begin
        if (rst) begin
            req_q <= 1'b0;
            ack_q <= 1'b0;
            cnt   <= 2'd0;
        end else begin
            ack_q <= 1'b0;
            if (!req_q) begin
                if (start) begin
                    req_q <= 1'b1;
                    cnt   <= 2'd0;
                end
            end else if (ack_q) begin
                req_q <= 1'b0;
            end else if (cnt == 2'd1) begin
                ack_q <= 1'b1;
            end else begin
                cnt <= cnt + 2'd1;
            end
        end
    end

    assign req = req_q;
    assign ack = ack_q;
endmodule
