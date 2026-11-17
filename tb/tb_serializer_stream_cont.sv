`timescale 1ns/1ps

// Continuous (Raw PCS) mode of serializer_stream: WORDS=1 must accept the next
// word on the cycle that emits the last bit, so serial_valid has no bubble
// between words.
module tb_serializer_stream_cont #(
    parameter int DATA_WIDTH = 8
);
    localparam int NWORDS = 4;

    logic clk, rst_n, enable;
    logic in_valid, in_ready;
    logic [DATA_WIDTH-1:0] in_word;
    logic serial_out, serial_valid, busy, frame_done;

    int errors;
    int bit_index;
    logic [NWORDS*DATA_WIDTH-1:0] expected;
    logic [NWORDS*DATA_WIDTH-1:0] received;
    int gaps;

    always #5 clk = ~clk;

    serializer_stream #(
        .DATA_WIDTH(DATA_WIDTH), .WORDS(1), .LSB_FIRST(1'b1)
    ) dut (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .in_valid(in_valid), .in_ready(in_ready), .in_word(in_word),
        .serial_out(serial_out), .serial_valid(serial_valid),
        .busy(busy), .frame_done(frame_done)
    );

    // Expected serial stream, LSB first per word.
    initial begin
        logic [7:0] wv;
        expected = '0;
        for (int w = 0; w < NWORDS; w++) begin
            wv = 8'h11 * (w + 1);
            for (int b = 0; b < DATA_WIDTH; b++)
                expected[w*DATA_WIDTH + b] = wv[b];
        end
    end

    always @(posedge clk) begin
        if (rst_n && serial_valid) begin
            received[bit_index] <= serial_out;
            bit_index <= bit_index + 1;
        end
    end

    // Count serial_valid deassertions after the stream has started.
    logic started;
    always @(posedge clk) begin
        if (!rst_n) begin
            started <= 1'b0;
            gaps <= 0;
        end else if (serial_valid) begin
            started <= 1'b1;
        end else if (started && bit_index < NWORDS*DATA_WIDTH) begin
            gaps <= gaps + 1;
        end
    end

    initial begin
        clk = 0; rst_n = 0; enable = 1'b1;
        in_valid = 1'b0; in_word = '0;
        errors = 0; bit_index = 0; gaps = 0; started = 1'b0;
        received = '0;
        repeat (4) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        for (int w = 0; w < NWORDS; w++) begin
            @(negedge clk);
            in_word  = 8'h11 * (w + 1);
            in_valid = 1'b1;
            // Hold until accepted.
            while (!in_ready) @(negedge clk);
            @(negedge clk);
            // Keep presenting the next word on the following cycle (the
            // gapless path accepts it while the last bit is emitted).
        end
        in_valid = 1'b0;

        wait (bit_index >= NWORDS*DATA_WIDTH);
        repeat (2) @(posedge clk);

        if (gaps != 0) begin
            $display("FAIL: %0d serial_valid gaps between words", gaps);
            errors++;
        end
        if (received !== expected) begin
            $display("FAIL: bit stream got %0h want %0h", received, expected);
            errors++;
        end
        if (errors == 0)
            $display("PASS: tb_serializer_stream_cont words=%0d gaps=%0d",
                     NWORDS, gaps);
        else
            $display("FAIL: tb_serializer_stream_cont errors=%0d", errors);
        $finish;
    end

    initial begin
        #100000;
        $display("FAIL: timeout bit_index=%0d", bit_index);
        $finish;
    end
endmodule
