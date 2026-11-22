// The state type of the request/acknowledge FSM (a 4-state enum: no initial value is known).
package fsm_pkg;
    typedef enum logic [1:0] {IDLE, BUSY, DONE} state_t;
endpackage
