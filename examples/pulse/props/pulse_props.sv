module pulse_props(input logic clk, rst, level, pulse_out);
    default clocking @(posedge clk); endclocking
    default disable iff (rst);

    a_rise: assert property ($rose(level) |=> pulse_out);
    a_no_rise: assert property (!$rose(level) |=> !pulse_out);
    a_width: assert property (pulse_out |=> !pulse_out);

    c_pulse: cover property ($rose(level) ##1 pulse_out ##1 !pulse_out);
endmodule

bind pulse pulse_props u_props (
    .clk(clk), .rst(rst), .level(level), .pulse_out(pulse_out)
);
