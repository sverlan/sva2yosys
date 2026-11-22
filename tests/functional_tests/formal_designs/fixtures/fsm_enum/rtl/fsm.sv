// A request/acknowledge FSM: IDLE -req-> BUSY -ack-> DONE -> IDLE; synchronous reset to IDLE.
// (Yosys reads neither a package import in the module header nor one in its body: scoped names.)
// The state has no initial value: BMC starts from any state, including the unused encoding 3.
module fsm (
    input  logic            clk,
    input  logic            rst,
    input  logic            req,
    input  logic            ack,
    output fsm_pkg::state_t state
);

    always_ff @(posedge clk) begin
        if (rst) state <= fsm_pkg::IDLE;
        else
            case (state)
                fsm_pkg::IDLE:    if (req) state <= fsm_pkg::BUSY;
                fsm_pkg::BUSY:    if (ack) state <= fsm_pkg::DONE;
                fsm_pkg::DONE:    state <= fsm_pkg::IDLE;
                default: state <= fsm_pkg::IDLE;
            endcase
    end
endmodule
