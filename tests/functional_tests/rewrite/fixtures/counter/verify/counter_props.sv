// Properties of the counter, bound into every counter instance.
module counter_props (
    input logic       clk,
    input logic       rst,
    input logic       en,
    input logic [3:0] cnt
);
    default clocking cb @(posedge clk);
    endclocking
    default disable iff (rst);

    // Helper logic stays as written.
    logic at_max;
    assign at_max = cnt == 4'd15;

    sequence counting;
        en && !at_max;
    endsequence

    property steps;
        counting |=> cnt == $past(cnt) + 4'd1;
    endproperty

    a_steps: assert property (steps);
    a_holds: assert property (!en |=> $stable(cnt));  // trailing comment stays
    c_wraps: cover property (
        at_max ##1 cnt == 4'd0
    );
endmodule

bind counter counter_props u_props (
    .clk(clk), .rst(rst), .en(en), .cnt(cnt)
);
