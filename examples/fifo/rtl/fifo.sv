// Interface: two entries; full blocks push and empty blocks pop independently.
module fifo(
    input logic clk, rst, push, pop,
    input logic [7:0] data_in,
    output logic [7:0] data_out,
    output logic [1:0] occupancy,
    output logic full, empty
);
    logic [7:0] slot0 = 8'd0;
    logic [7:0] slot1 = 8'd0;
    logic read_index = 1'b0;
    logic write_index = 1'b0;
    logic [1:0] count_q = 2'd0;

    wire push_accepted = push && !full;
    wire pop_accepted = pop && !empty;

    assign occupancy = count_q;
    assign full = count_q == 2'd2;
    assign empty = count_q == 2'd0;
`ifdef EXAMPLE_BUG
    assign data_out = (read_index ? slot1 : slot0) ^ 8'd1;
`else
    assign data_out = read_index ? slot1 : slot0;
`endif

    always_ff @(posedge clk) begin
        if (rst) begin
            read_index <= 1'b0;
            write_index <= 1'b0;
            count_q <= 2'd0;
        end else begin
            case ({push_accepted, pop_accepted})
                2'b10: count_q <= count_q + 2'd1;
                2'b01: count_q <= count_q - 2'd1;
                default: ;
            endcase
            if (push_accepted) begin
                if (write_index)
                    slot1 <= data_in;
                else
                    slot0 <= data_in;
                write_index <= !write_index;
            end
            if (pop_accepted)
                read_index <= !read_index;
        end
    end
endmodule
