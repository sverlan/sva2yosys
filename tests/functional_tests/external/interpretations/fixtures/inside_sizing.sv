module top;
  logic [3:0] a = 8, b = 8;
  logic signed [3:0] s = -1;
  initial begin
    #1;
    $display("SVA2YOSYS_PROBE fill %0d", 4'd15 inside {'1, 8'h00});
    $display("SVA2YOSYS_PROBE arith %0d", (a+b) inside {4'd0, 8'd17});
    $display("SVA2YOSYS_PROBE sign %0d", s inside {-8'sd1, 8'd200});
    $display("SVA2YOSYS_PROBE uniform_hit %0d", a inside {4'd7, 4'd8});
    $display("SVA2YOSYS_PROBE uniform_miss %0d", a inside {4'd6, 4'd7});
    $display("SVA2YOSYS_PROBE done 1");
    $finish;
  end
endmodule
