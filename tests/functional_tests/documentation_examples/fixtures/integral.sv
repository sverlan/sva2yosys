module top(input logic clk, input int count,
           input logic [3:0] v, input logic signed [7:0] s);
    typedef enum logic [1:0] {IDLE, BUSY, DONE} state_t;
    state_t state;
    typedef struct packed {logic a; logic [2:0] b;} record_t;
    record_t record;
    types: assert property (@(posedge clk) count >= 0 && state != DONE);
    whole: assert property (@(posedge clk) record == v);
    operators: assert property (@(posedge clk)
        v[2] && v[3:2] == 2'b10 && (s >>> 1) <= s);
endmodule
