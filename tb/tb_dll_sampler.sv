`timescale 1ns/1ps
// Phase-selection check for dll_sampler_model.
//
// The test drives data_in with a step at a known offset after a clk edge and
// then checks which taps see the old value and which see the new one.  A
// sampler that evaluates data_in at the edge (the `<= #delay` mistake) returns
// the old value for every tap and fails this test; a correct phase sampler
// flips at the tap whose sampling instant crosses the step.
//
// Two step positions are used so every tap is checked on both sides:
//   offset 5.0 ns: taps 0..3 (0.625..4.375) old, taps 4..7 (5.625..9.375) new
//   offset 8.0 ns: taps 0..5 (0.625..6.875) old, taps 6..7 (8.125..9.375) new
module tb_dll_sampler;
    localparam real SLOT_NS = 1.25;

    logic clk, rst_n, data_in;
    logic [2:0] tap;
    logic smp;
    int errors;

    always #5 clk = ~clk;

    dll_sampler_model #(.SLOT_NS(SLOT_NS)) dut (
        .clk(clk),
        .rst_n(rst_n),
        .data_in(data_in),
        .tap(tap),
        .smp(smp)
    );

    task automatic check_offset(input real offset, input int first_new);
        int exp;
        for (int t = 0; t < 8; t++) begin
            tap = t[2:0];
            data_in = 1'b0;
            @(posedge clk);
            #(offset) data_in = 1'b1;
            // The sample for this edge lands at tap_phase; the core reads it
            // on the next edge, so inspect it just after that edge.
            @(posedge clk);
            #0.05;
            exp = (t >= first_new) ? 1 : 0;
            if (smp !== exp[0]) begin
                $display("FAIL: offset=%0.2f tap=%0d smp=%0b expected=%0b",
                         offset, t, smp, exp);
                errors++;
            end
            // Let the new edge's delayed read complete and the line settle
            // before the next tap starts.
            #10;
        end
    endtask

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        data_in = 1'b0;
        tap = 3'd0;
        errors = 0;
        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        check_offset(5.0, 4);
        check_offset(8.0, 6);

        if (errors != 0) begin
            $display("FAIL: tb_dll_sampler errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_dll_sampler phase selection verified (8 taps, 2 step positions)");
        $finish;
    end
endmodule
