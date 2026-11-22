module counter_props(input logic clk, rst, enable, input logic [1:0] value);
    default clocking @(posedge clk); endclocking

    // No default disable: the reset obligation must itself remain active.
    a_increment: assert property (
        disable iff (rst) enable |=> value == ($past(value) + 2'd1)
    );
    a_hold: assert property (
        disable iff (rst) !enable |=> $stable(value)
    );
    a_reset: assert property (rst |=> value == 2'd0);

    c_wrap: cover property (
        disable iff (rst)
        (value == 2'd0) ##1 (value == 2'd1) ##1 (value == 2'd2)
        ##1 (value == 2'd3) ##1 (value == 2'd0)
    );
endmodule

bind counter counter_props u_props (
    .clk(clk), .rst(rst), .enable(enable), .value(value)
);
