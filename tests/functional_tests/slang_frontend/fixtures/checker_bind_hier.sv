// checker with arguments, bind of a property module, hierarchical reference.
checker c_handshake(logic clk, logic req, logic ack);
    c_assert: assert property (@(posedge clk) req |=> ack);
endchecker

module dut (input logic clk, input logic req, output logic ack);
    logic busy;
    always_ff @(posedge clk) begin
        ack  <= req;
        busy <= req;
    end
endmodule

module dut_props (input logic clk, input logic req, input logic ack);
    b_assert: assert property (@(posedge clk) req |=> ack);
endmodule

module top (input logic clk, input logic req);
    logic ack;
    dut u_dut (.clk, .req, .ack);
    c_handshake u_chk (clk, req, ack);
    h_assert: assert property (@(posedge clk) u_dut.busy |-> ##1 1'b1);
endmodule

bind dut dut_props u_props (.clk, .req, .ack);
