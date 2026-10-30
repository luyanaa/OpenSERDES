`timescale 1ns/1ps
module tb_64b66b_gearbox;
    logic clk=0, rst_n=0, enable=1, block_valid=0, block_ready, word_valid, word_ready=1;
    logic [65:0] block_in=0;
    logic [63:0] word_out;
    int blocks=0, words=0, errors=0;
    always #5 clk=~clk;

    pcs_64b66b_gearbox dut(
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .block_in(block_in), .block_valid(block_valid), .block_ready(block_ready),
        .word_out(word_out), .word_valid(word_valid), .word_ready(word_ready)
    );

    always @(posedge clk) if (word_valid && word_ready) words++;

    initial begin
        repeat (3) @(posedge clk); rst_n=1; repeat (2) @(posedge clk);
        for (int i=0; i<33; i++) begin
            @(negedge clk);
            block_in = {2'b01, 64'(i * 64'h9E3779B97F4A7C15)};
            block_valid = 1;
            while (!block_ready) @(negedge clk);
            @(posedge clk); #1;
            block_valid = 0;
            blocks++;
        end
        repeat (20) @(posedge clk);
        // 33 blocks = 2178 bits -> 34 words with 2 bits left over.
        if (words != 34) begin
            $display("FAIL gearbox words=%0d expected=34", words);
            errors++;
        end
        if (errors) $display("FAIL gearbox errors=%0d", errors);
        else $display("PASS gearbox 33 blocks -> %0d words", words);
        $finish;
    end
    initial begin #20000; $display("FAIL timeout"); $finish; end
endmodule
