// Properties over an enum-typed state, compared with the enum's members.
module fsm_props (
    input logic            clk,
    input logic            rst,
    input logic            req,
    input logic            ack,
    input fsm_pkg::state_t state
);

    a_start: assert property (@(posedge clk) disable iff (rst)
        state == fsm_pkg::IDLE && req |=> state == fsm_pkg::BUSY);
    a_finish: assert property (@(posedge clk) state == fsm_pkg::DONE |=> state == fsm_pkg::IDLE);
    // The previous state is read with $past (the enum is 4-state, so its first sample is X);
    // BUSY -> DONE needs the acknowledge sampled at the previous tick.
    a_done_by_ack: assert property (@(posedge clk)
        $past(state) == fsm_pkg::BUSY && state == fsm_pkg::DONE |-> $past(ack));
    c_cycle: cover property (@(posedge clk) disable iff (rst)
        state == fsm_pkg::IDLE ##1 state == fsm_pkg::BUSY ##1 state == fsm_pkg::DONE);
endmodule

bind fsm fsm_props u_props (.*);
