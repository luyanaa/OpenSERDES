`timescale 1ns/1ps

// Bit-exact comparison of serializer_stream (bit clock) and the half-rate
// serializer (word clock + 2x bit clock).
module tb_serializer_half_rate #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8,
    parameter bit LSB_FIRST  = 1'b1
);

    localparam int N_BITS = DATA_WIDTH * WORDS;
    localparam int FRAMES = 40;

    logic clk_bit;
    logic clk_word;
    logic rst_n;
    logic in_valid_ref;
    logic in_ready_ref;
    logic in_valid_half;
    logic in_ready_half;
    logic [DATA_WIDTH-1:0] in_word_ref;
    logic [DATA_WIDTH-1:0] in_word_half;
    logic serial_ref, serial_half;
    logic valid_ref, valid_half;
    logic busy_ref, busy_half;
    logic done_ref, done_half;

    int errors;
    int frames_ref;
    int frames_half;

    always #2.5 clk_bit = ~clk_bit;
    always @(posedge clk_bit) clk_word <= ~clk_word;

    serializer_stream #(
        .DATA_WIDTH(DATA_WIDTH), .WORDS(WORDS), .LSB_FIRST(LSB_FIRST)
    ) u_ref (
        .clk(clk_bit), .rst_n(rst_n), .enable(1'b1),
        .in_valid(in_valid_ref), .in_ready(in_ready_ref), .in_word(in_word_ref),
        .serial_out(serial_ref), .serial_valid(valid_ref),
        .busy(busy_ref), .frame_done(done_ref)
    );

    serializer_half_rate #(
        .DATA_WIDTH(DATA_WIDTH), .WORDS(WORDS), .LSB_FIRST(LSB_FIRST)
    ) u_half (
        .clk_word(clk_word), .clk_bit(clk_bit), .rst_n(rst_n),
        .in_valid(in_valid_half), .in_ready(in_ready_half), .in_word(in_word_half),
        .serial_out(serial_half), .serial_valid(valid_half),
        .busy(busy_half), .frame_done(done_half)
    );

    always @(posedge clk_bit) begin
        if (done_ref)
            frames_ref <= frames_ref + 1;
        if (done_half)
            frames_half <= frames_half + 1;
    end

    task automatic send_ref(input logic [DATA_WIDTH-1:0] w);
        begin
            @(negedge clk_bit);
            in_word_ref = w;
            in_valid_ref = 1'b1;
            while (!in_ready_ref) @(negedge clk_bit);
            @(posedge clk_bit);
            #1;
            in_valid_ref = 1'b0;
        end
    endtask

    task automatic send_half(input logic [DATA_WIDTH-1:0] w);
        begin
            @(negedge clk_word);
            in_word_half = w;
            in_valid_half = 1'b1;
            while (!in_ready_half) @(negedge clk_word);
            @(posedge clk_word);
            #1;
            in_valid_half = 1'b0;
        end
    endtask

    task automatic collect_ref(output logic [N_BITS-1:0] bits);
        int got;
        begin
            got = 0;
            for (int cyc = 0; cyc < N_BITS * 4 && got < N_BITS; cyc++) begin
                @(posedge clk_bit);
                #1;
                if (valid_ref) begin
                    bits[got] = serial_ref;
                    got++;
                end
            end
            if (got < N_BITS) begin
                $display("ERROR: ref serial timeout got=%0d", got);
                errors++;
            end
        end
    endtask

    task automatic collect_half(output logic [N_BITS-1:0] bits);
        int got;
        begin
            got = 0;
            for (int cyc = 0; cyc < N_BITS * 8 && got < N_BITS; cyc++) begin
                @(posedge clk_bit);
                #1;
                if (valid_half) begin
                    bits[got] = serial_half;
                    got++;
                end
            end
            if (got < N_BITS) begin
                $display("ERROR: half serial timeout got=%0d", got);
                errors++;
            end
        end
    endtask

    initial begin
        logic [DATA_WIDTH-1:0] frame [0:WORDS-1];
        logic [N_BITS-1:0] expected;
        logic [N_BITS-1:0] got_ref;
        logic [N_BITS-1:0] got_half;
        clk_bit       = 1'b0;
        clk_word      = 1'b0;
        rst_n         = 1'b0;
        in_valid_ref  = 1'b0;
        in_valid_half = 1'b0;
        in_word_ref   = '0;
        in_word_half  = '0;
        errors        = 0;
        frames_ref    = 0;
        frames_half   = 0;

        repeat (3) @(posedge clk_bit);
        rst_n = 1'b1;
        repeat (4) @(posedge clk_bit);

        for (int f = 0; f < FRAMES; f++) begin
            for (int w = 0; w < WORDS; w++)
                frame[w] = $urandom;
            // Expected serial order is computed before collection starts.
            for (int w = 0; w < WORDS; w++) begin
                for (int b = 0; b < DATA_WIDTH; b++) begin
                    expected[w*DATA_WIDTH + b] = LSB_FIRST
                        ? frame[w][b] : frame[w][DATA_WIDTH-1-b];
                end
            end
            fork
                begin
                    for (int w = 0; w < WORDS; w++)
                        send_ref(frame[w]);
                end
                collect_ref(got_ref);
            join
            fork
                begin
                    for (int w = 0; w < WORDS; w++)
                        send_half(frame[w]);
                end
                collect_half(got_half);
            join
            if (got_ref !== expected) begin
                $display("ERROR: ref frame %0d mismatch", f);
                errors++;
            end
            if (got_half !== expected) begin
                $display("ERROR: half frame %0d mismatch got=%h", f, got_half);
                errors++;
            end
            if (done_half !== 1'b0 && frames_half == 0) begin
                $display("ERROR: half frame_done before first frame");
                errors++;
            end
        end
        repeat (N_BITS * 2) @(posedge clk_bit);
        #1;
        if (frames_ref != frames_half || frames_ref < FRAMES) begin
            $display("ERROR: frame_done ref=%0d half=%0d", frames_ref, frames_half);
            errors++;
        end
        if (errors != 0) begin
            $display("FAIL: tb_serializer_half_rate errors=%0d", errors);
            $fatal(1);
        end
        $display("PASS: tb_serializer_half_rate DATA_WIDTH=%0d WORDS=%0d LSB=%0d frames=%0d",
                 DATA_WIDTH, WORDS, LSB_FIRST, frames_ref);
        $finish;
    end

endmodule
