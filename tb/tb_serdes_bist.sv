`timescale 1ns/1ps

module tb_serdes_bist #(
    parameter int WIDTH = 7
);

    logic clk;
    logic rst_n;
    logic start;
    logic clear;
    logic serial_out;
    logic serial_in;
    logic inject;
    logic error;
    logic [31:0] error_count;
    logic [31:0] bit_count;

    int errors;

    always #5 clk = ~clk;

    assign serial_in = serial_out ^ inject;

    serdes_bist #(.WIDTH(WIDTH)) dut (
        .clk(clk), .rst_n(rst_n), .start(start), .clear(clear),
        .serial_out(serial_out), .serial_in(serial_in),
        .error(error), .error_count(error_count), .bit_count(bit_count)
    );

    initial begin
        clk     = 1'b0;
        rst_n   = 1'b0;
        start   = 1'b0;
        clear   = 1'b0;
        inject  = 1'b0;
        errors  = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        @(negedge clk); clear = 1'b1;
        @(posedge clk); #1;
        @(negedge clk); clear = 1'b0;
        start = 1'b1;

        repeat (2000) @(posedge clk);
        #1;
        if (error) begin
            $display("ERROR: BIST error on clean loopback");
            errors++;
        end
        if (bit_count < 2000 || bit_count > 2001) begin
            $display("ERROR: BIST bit_count=%0d", bit_count);
            errors++;
        end

        // Pause and resume without clear: generator and checker must stay
        // aligned and must not count the paused cycles.
        begin
            logic [31:0] count_before;
            count_before = bit_count;
            @(negedge clk); start = 1'b0;
            repeat (5) @(posedge clk);
            @(negedge clk); start = 1'b1;
            repeat (20) @(posedge clk);
            #1;
            if (bit_count != count_before + 20) begin
                $display("ERROR: BIST pause accounting before=%0d after=%0d",
                         count_before, bit_count);
                errors++;
            end
            if (error) begin
                $display("ERROR: BIST error after pause/resume");
                errors++;
            end
        end

        @(negedge clk); inject = 1'b1;
        @(posedge clk); #1;
        @(negedge clk); inject = 1'b0;
        @(posedge clk); #1;
        if (!error || error_count != 1) begin
            $display("ERROR: BIST injection not counted error=%b count=%0d",
                     error, error_count);
            errors++;
        end

        @(negedge clk); clear = 1'b1;
        @(posedge clk); #1;
        @(negedge clk); clear = 1'b0;
        if (error || error_count != 0 || bit_count != 0) begin
            $display("ERROR: BIST clear failed");
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_serdes_bist WIDTH=%0d errors=%0d", WIDTH, errors);
            $fatal(1);
        end
        $display("PASS: tb_serdes_bist WIDTH=%0d", WIDTH);
        $finish;
    end

endmodule
