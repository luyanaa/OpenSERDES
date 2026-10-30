`timescale 1ns/1ps
// Bit-exact check of the 66->64 gearbox reservoir.
//
// A stream of N 66-bit blocks is concatenated LSB-first into one bit stream;
// the DUT must emit the same stream as 64-bit words (LSB-first), in order,
// preserving every bit.  We model the expected stream in a wide array and
// compare each emitted word's relevant bits, plus exercise random backpressure
// on word_ready and a continuous-block input.
module tb_64b66b_gearbox;
    localparam int NBLK = 33;                 // 33*66 = 2178 bits
    localparam int NBITS = NBLK * 66;
    logic clk=0, rst_n=0, enable=1, block_valid=0, block_ready, word_valid, word_ready=0;
    logic [65:0] block_in=0;
    logic [63:0] word_out;
    int blocks=0, words=0, errors=0;
    // Expected bit stream, bit i of exp_stream is the i-th bit pushed in.
    logic [NBITS-1:0] exp_stream = '0;
    int exp_idx = 0;
    int recv_idx = 0;
    always #5 clk=~clk;

    pcs_64b66b_gearbox dut(
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .block_in(block_in), .block_valid(block_valid), .block_ready(block_ready),
        .word_out(word_out), .word_valid(word_valid), .word_ready(word_ready)
    );

    // Capture each emitted 64-bit word and compare bit-for-bit.
    always @(posedge clk) if (rst_n && word_valid && word_ready) begin
        for (int b = 0; b < 64; b++) begin
            if (recv_idx + b < NBITS) begin
                if (word_out[b] !== exp_stream[recv_idx + b]) begin
                    $display("FAIL bit %0d: got %b exp %b (word %0d)",
                             recv_idx+b, word_out[b], exp_stream[recv_idx+b], words);
                    errors++;
                end
            end
        end
        recv_idx += 64;
        words++;
    end

    // Drive blocks; random word_ready backpressure.
    initial begin
        logic [63:0] payload;
        repeat (3) @(posedge clk); rst_n=1; repeat (2) @(posedge clk);
        // toggle word_ready randomly each cycle for backpressure coverage
        fork
            begin : bp
                forever begin
                    @(negedge clk); word_ready = ($urandom_range(0,3) != 0); // 75% ready
                end
            end
        join_none

        for (int i=0; i<NBLK; i++) begin
            payload = 64'(i) * 64'h9E3779B97F4A7C15 + 64'h12345;
            block_in = {2'b01, payload};
            // record expected stream: block_in[0] (sync LSB) first
            for (int b=0; b<66; b++) exp_stream[exp_idx+b] = block_in[b];
            exp_idx += 66;
            @(negedge clk);
            block_valid = 1;
            while (!block_ready) @(negedge clk);
            @(posedge clk); #1;
            block_valid = 0;
            blocks++;
        end
        // drain
        word_ready = 1;
        repeat (40) @(posedge clk);
        if (words != 34) begin
            $display("FAIL gearbox words=%0d expected=34", words);
            errors++;
        end
        if (recv_idx != 34*64) begin
            $display("FAIL recv bits=%0d expected=%0d", recv_idx, 34*64);
            errors++;
        end
        if (errors) $display("FAIL gearbox errors=%0d", errors);
        else $display("PASS gearbox 33 blocks -> %0d words, bit-exact, backpressure ok", words);
        $finish;
    end
    initial begin #200000; $display("FAIL timeout"); $finish; end
endmodule
