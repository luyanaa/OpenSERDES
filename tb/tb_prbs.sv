`timescale 1ns/1ps

module tb_prbs #(
    parameter int WIDTH = 7
);

    logic clk;
    logic rst_n;
    logic enable;
    logic clear;
    logic bit_gen;
    logic bit_check;
    logic inject;
    logic error;
    logic [31:0] error_count;
    logic [31:0] bit_count;

    int errors;

    always #5 clk = ~clk;

    prbs_gen #(.WIDTH(WIDTH), .SEED('1)) u_gen (
        .clk(clk), .rst_n(rst_n), .clear(clear), .enable(enable), .bit_out(bit_gen)
    );

    assign bit_check = bit_gen ^ inject;

    prbs_check #(.WIDTH(WIDTH), .SEED('1)) u_check (
        .clk(clk), .rst_n(rst_n), .clear(clear), .enable(enable),
        .bit_in(bit_check), .error(error), .error_count(error_count),
        .bit_count(bit_count)
    );

    initial begin
        clk     = 1'b0;
        rst_n   = 1'b0;
        enable  = 1'b0;
        clear   = 1'b0;
        inject  = 1'b0;
        errors  = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);
        enable = 1'b1;

        repeat (2000) @(posedge clk);
        #1;
        if (error) begin
            $display("ERROR: PRBS%0d checker error on clean stream", WIDTH);
            errors++;
        end
        if (bit_count < 2000 || bit_count > 2001) begin
            $display("ERROR: PRBS%0d bit_count=%0d", WIDTH, bit_count);
            errors++;
        end

        // Inject one error.
        @(negedge clk); inject = 1'b1;
        @(posedge clk); #1;
        @(negedge clk); inject = 1'b0;
        @(posedge clk); #1;
        if (!error || error_count != 1) begin
            $display("ERROR: PRBS%0d error injection not counted: error=%b count=%0d",
                     WIDTH, error, error_count);
            errors++;
        end

        @(negedge clk); clear = 1'b1;
        @(posedge clk); #1;
        @(negedge clk); clear = 1'b0;
        if (error || error_count != 0 || bit_count != 0) begin
            $display("ERROR: PRBS%0d clear failed", WIDTH);
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_prbs WIDTH=%0d errors=%0d", WIDTH, errors);
            $fatal(1);
        end
        $display("PASS: tb_prbs WIDTH=%0d", WIDTH);
        $finish;
    end

endmodule
