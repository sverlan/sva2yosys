module fifo_props(
    input logic clk, rst, push, pop,
    input logic [7:0] data_in, data_out,
    input logic [1:0] occupancy,
    input logic full, empty
);
    // Shift-based reference queue: no pointer or slot selection from the RTL.
    logic [1:0] reference_count = 2'd0;
    logic [7:0] reference_head = 8'd0;
    logic [7:0] reference_tail = 8'd0;
    wire accept_push = push && reference_count < 2'd2;
    wire accept_pop = pop && reference_count != 2'd0;

    always_ff @(posedge clk) begin
        if (rst) begin
            reference_count <= 2'd0;
            reference_head <= 8'd0;
            reference_tail <= 8'd0;
        end else begin
            case ({accept_push, accept_pop})
                2'b10: begin
                    if (reference_count == 2'd0)
                        reference_head <= data_in;
                    else
                        reference_tail <= data_in;
                    reference_count <= reference_count + 2'd1;
                end
                2'b01: begin
                    reference_head <= reference_tail;
                    reference_count <= reference_count - 2'd1;
                end
                // Both can be accepted only at occupancy one.
                2'b11: reference_head <= data_in;
                default: ;
            endcase
        end
    end

    default clocking @(posedge clk); endclocking

    // Counts/flags agree even across reset; no disable hides these comparisons.
    a_occupancy: assert property (occupancy == reference_count);
    a_flags: assert property (
        (full == (reference_count == 2'd2)) && (empty == (reference_count == 2'd0))
    );
    a_bounds: assert property (occupancy <= 2'd2);
    a_order: assert property (
        disable iff (rst) (reference_count != 2'd0) |-> (data_out == reference_head)
    );

    c_round_trip: cover property (
        disable iff (rst)
        (empty && push && !pop) ##1 (!empty && pop) ##1 empty
    );
    c_fill_drain: cover property (
        disable iff (rst)
        (empty && push && !pop) ##1 (!full && !empty && push && !pop)
        ##1 (full && pop) ##1 (!empty && pop) ##1 empty
    );
endmodule

bind fifo fifo_props u_props (
    .clk(clk), .rst(rst), .push(push), .pop(pop),
    .data_in(data_in), .data_out(data_out), .occupancy(occupancy),
    .full(full), .empty(empty)
);
