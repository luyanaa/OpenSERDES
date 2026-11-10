`timescale 1ns/1ps

// Ping-pong serializer test: drive back-to-back frames and check every serial
// bit against the frame that was loaded, honouring LSB/MSB word/bit order.
module tb_serializer_pingpong #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8,
    parameter bit LSB_FIRST  = 1'b1,
    parameter int FRAMES     = 40
);

    localparam int RATIO  = DATA_WIDTH * WORDS;
    localparam int N_BITS = FRAMES * RATIO;

    logic clk;
    logic rst_n;
    logic enable;
    logic load;
    logic [WORDS-1:0][DATA_WIDTH-1:0] par_data;
    logic ready;
    logic serial_out;
    logic serial_valid;
    logic busy;
    logic frame_done;

    logic [N_BITS-1:0] exp_bits;
    int errors;
    int sent;
    int got;
    int frames_done;

    serializer_pingpong #(
        .DATA_WIDTH(DATA_WIDTH), .WORDS(WORDS), .LSB_FIRST(LSB_FIRST)
    ) dut (
        .clk(clk), .rst_n(rst_n), .enable(enable), .load(load),
        .par_data(par_data), .ready(ready), .serial_out(serial_out),
        .serial_valid(serial_valid), .busy(busy), .frame_done(frame_done)
    );

    always #5 clk = ~clk;

    // Build the expected bit stream for every frame.
    initial begin
        logic [WORDS-1:0][DATA_WIDTH-1:0] d;
        logic [DATA_WIDTH-1:0] wd;
        int p;
        p = 0;
        for (int f = 0; f < FRAMES; f++) begin
            for (int w = 0; w < WORDS; w++) begin
                d[w] = (f * 37 + w * 11 + 3);
            end
            for (int w = 0; w < WORDS; w++) begin
                wd = d[w];
                for (int b = 0; b < DATA_WIDTH; b++) begin
                    exp_bits[p] = LSB_FIRST ? wd[b] : wd[DATA_WIDTH-1-b];
                    p++;
                end
            end
        end
    end

    // Frame source: present a new frame whenever the DUT is ready.
    initial begin
        logic [WORDS-1:0][DATA_WIDTH-1:0] d;
        sent = 0;
        par_data = '0;
        load = 1'b0;
        wait (rst_n);
        @(negedge clk);
        while (sent < FRAMES) begin
            @(negedge clk);
            if (ready && sent < FRAMES) begin
                for (int w = 0; w < WORDS; w++)
                    d[w] = (sent * 37 + w * 11 + 3);
                par_data = d;
                load = 1'b1;
                sent++;
            end else begin
                load = 1'b0;
            end
        end
        @(negedge clk);
        load = 1'b0;
    end

    // Bit checker.
    initial begin
        errors = 0;
        got = 0;
        frames_done = 0;
        wait (rst_n);
        while (got < N_BITS) begin
            @(posedge clk);
            #1;
            if (serial_valid) begin
                if (serial_out !== exp_bits[got]) begin
                    if (errors < 12)
                        $display("ERROR: bit %0d got %0b expected %0b", got,
                                 serial_out, exp_bits[got]);
                    errors++;
                end
                got++;
            end
            if (frame_done)
                frames_done++;
        end
        #200;
        if (errors != 0) begin
            $display("FAIL: tb_serializer_pingpong DATA_WIDTH=%0d WORDS=%0d LSB=%0d errors=%0d got=%0d frames=%0d",
                     DATA_WIDTH, WORDS, LSB_FIRST, errors, got, frames_done);
            $fatal(1);
        end
        $display("PASS: tb_serializer_pingpong DATA_WIDTH=%0d WORDS=%0d LSB=%0d frames=%0d bits=%0d",
                 DATA_WIDTH, WORDS, LSB_FIRST, frames_done, got);
        $finish;
    end

    initial begin
        clk    = 1'b0;
        rst_n  = 1'b0;
        enable = 1'b1;
        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (200000) @(posedge clk);
        $display("FAIL: tb_serializer_pingpong timeout");
        $fatal(1);
    end

endmodule
