`timescale 1ns/1ps

// Pair-fed half-rate deserializer test.  The sampler is intentionally outside:
// each word clock presents two serial bits (bit0 first, bit1 second).
module tb_deserializer_half_rate #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8,
    parameter bit LSB_FIRST  = 1'b1
);

    localparam int N_BITS = DATA_WIDTH * WORDS;

    logic clk;
    logic rst_n;
    logic clear;
    logic enable;
    logic pair_valid;
    logic bit0;
    logic bit1;
    logic [WORDS-1:0][DATA_WIDTH-1:0] par_data;
    logic [N_BITS-1:0] par_flat;
    logic par_valid;
    logic busy;

    int errors;
    int frames;

    assign par_flat = par_data;

    always #5 clk = ~clk;

    deserializer_half_rate #(
        .DATA_WIDTH(DATA_WIDTH), .WORDS(WORDS), .LSB_FIRST(LSB_FIRST)
    ) dut (
        .clk_word(clk), .rst_n(rst_n), .enable(enable), .clear(clear),
        .pair_valid(pair_valid), .bit0(bit0), .bit1(bit1),
        .par_data(par_data), .par_valid(par_valid), .busy(busy)
    );

    task automatic send_pair(input logic b0, input logic b1);
        begin
            @(negedge clk);
            bit0 = b0;
            bit1 = b1;
            pair_valid = 1'b1;
            @(posedge clk);
            #1;
        end
    endtask

    initial begin
        logic [DATA_WIDTH-1:0] frame [0:WORDS-1];
        logic [N_BITS-1:0] bits;
        int p;
        clk        = 1'b0;
        rst_n      = 1'b0;
        clear      = 1'b0;
        enable     = 1'b1;
        pair_valid = 1'b0;
        bit0       = 1'b0;
        bit1       = 1'b0;
        errors     = 0;
        frames     = 0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        for (int f = 0; f < 50; f++) begin
            for (int w = 0; w < WORDS; w++)
                frame[w] = $urandom;
            for (int w = 0; w < WORDS; w++) begin
                for (int b = 0; b < DATA_WIDTH; b++) begin
                    bits[w*DATA_WIDTH + b] = LSB_FIRST
                        ? ((frame[w] >> b) & 1'b1)
                        : ((frame[w] >> (DATA_WIDTH-1-b)) & 1'b1);
                end
            end
            for (p = 0; p < N_BITS/2; p++)
                send_pair(bits[2*p], bits[2*p+1]);
            @(negedge clk);
            pair_valid = 1'b0;
            // par_valid is registered on the last pair edge.
            while (!par_valid) @(posedge clk);
            #1;
            for (int w = 0; w < WORDS; w++) begin
                if (par_flat[w*DATA_WIDTH +: DATA_WIDTH] !== frame[w]) begin
                    $display("ERROR: frame %0d word %0d got %h expected %h",
                             f, w, par_flat[w*DATA_WIDTH +: DATA_WIDTH], frame[w]);
                    errors++;
                end
            end
            frames++;
            @(negedge clk);
        end

        // Clear while idle must not create a valid frame.
        @(negedge clk); clear = 1'b1;
        @(posedge clk); #1;
        @(negedge clk); clear = 1'b0;
        if (par_valid) begin
            $display("ERROR: par_valid high after clear");
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_deserializer_half_rate errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_deserializer_half_rate DATA_WIDTH=%0d WORDS=%0d LSB=%0d frames=%0d",
                 DATA_WIDTH, WORDS, LSB_FIRST, frames);
        $finish;
    end

endmodule
