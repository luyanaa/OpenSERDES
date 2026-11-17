`timescale 1ns/1ps

// End-to-end half-rate RX test: source-synchronous bit clock, comma-per-frame
// alignment, 8-bit words, LSB first.  The parallel bank must come out of
// clk_word with the frame's bit order preserved, and each frame must re-align
// on its own comma (ALIGN_MODE=2).
module tb_rx_half_rate #(
    parameter int DATA_WIDTH = 8,
    parameter int WORDS      = 4,
    parameter bit LSB_FIRST  = 1'b1
);

    localparam int N_BITS = DATA_WIDTH * WORDS;
    localparam logic [7:0] COMMA = 8'hBC;

    logic clk_bit;
    logic clk_word;
    logic rst_n;
    logic rx_enable;
    logic rx_flush;
    logic rx_align;
    logic rx_serial_in;
    logic phase_offset;
    logic [WORDS-1:0][DATA_WIDTH-1:0] rx_par_data;
    logic rx_par_valid;
    logic rx_busy;
    logic rx_lock;
    logic rx_lock_stale;
    logic rx_sample_strobe;
    logic rx_clk_out;
    logic rx_frame_locked;
    logic rx_frame_valid;
    logic rx_serial_data;
    logic rx_serial_valid;
    logic [11:0] rx_freq_trim;

    int errors;
    int frames;
    logic [N_BITS-1:0] par_flat;

    assign par_flat = rx_par_data;

    // clk_bit 50 MHz; clk_word is clk_bit / 2 with a small skew so the
    // bit-domain pair register settles before the word edge (the SDC models
    // this as a generated clock, not as a zero-delay loop).
    initial begin
        clk_bit  = 1'b0;
        clk_word = 1'b0;
    end
    always #10 clk_bit = ~clk_bit;
    initial begin
        forever begin
            @(posedge clk_bit);
            #1 clk_word = ~clk_word;
        end
    end

    serdes_rx_half_rate_digital #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST),
        .SYNC_STAGES(2),
        .ALIGN_MODE(2),
        .SYNC_LEN(8),
        .SYNC_PATTERN(COMMA),
        .PHASE_CTRL_W(1)
    ) dut (
        .clk_bit(clk_bit),
        .clk_word(clk_word),
        .rst_n(rst_n),
        .rx_enable(rx_enable),
        .rx_flush(rx_flush),
        .rx_align(rx_align),
        .rx_serial_in(rx_serial_in),
        .phase_offset(phase_offset),
        .rx_par_data(rx_par_data),
        .rx_par_valid(rx_par_valid),
        .rx_busy(rx_busy),
        .rx_lock(rx_lock),
        .rx_lock_stale(rx_lock_stale),
        .rx_sample_strobe(rx_sample_strobe),
        .rx_clk_out(rx_clk_out),
        .rx_frame_locked(rx_frame_locked),
        .rx_frame_valid(rx_frame_valid),
        .rx_serial_data(rx_serial_data),
        .rx_serial_valid(rx_serial_valid),
        .rx_freq_trim(rx_freq_trim)
    );

    task automatic send_bit(input logic b);
        begin
            @(negedge clk_bit);
            rx_serial_in = b;
        end
    endtask

    task automatic send_byte_lsb(input logic [7:0] value);
        begin
            for (int i = 0; i < 8; i++)
                send_bit(value[LSB_FIRST ? i : (7 - i)]);
        end
    endtask

    task automatic send_word(input logic [DATA_WIDTH-1:0] value);
        begin
            for (int i = 0; i < DATA_WIDTH; i++)
                send_bit(value[LSB_FIRST ? i : (DATA_WIDTH - 1 - i)]);
        end
    endtask

    task automatic send_frame(input logic [WORDS-1:0][DATA_WIDTH-1:0] words);
        begin
            send_byte_lsb(COMMA);
            for (int w = 0; w < WORDS; w++)
                send_word(words[w]);
            // Tail bits keep the line busy while the last pair drains.
            for (int i = 0; i < 4; i++)
                send_bit(1'b0);
        end
    endtask

    initial begin
        logic [WORDS-1:0][DATA_WIDTH-1:0] frame0;
        logic [WORDS-1:0][DATA_WIDTH-1:0] frame1;
        errors = 0;
        frames = 0;
        rst_n = 1'b0;
        rx_enable = 1'b0;
        rx_flush = 1'b0;
        rx_align = 1'b0;
        rx_serial_in = 1'b0;
        phase_offset = 1'b0;

        for (int w = 0; w < WORDS; w++) begin
            frame0[w] = DATA_WIDTH'(8'hA5 + 8'h11 * w);
            frame1[w] = DATA_WIDTH'(8'h11 + 8'h22 * w);
        end

        repeat (5) @(posedge clk_bit);
        rst_n = 1'b1;
        repeat (2) @(posedge clk_bit);
        rx_enable = 1'b1;

        fork
            send_frame(frame0);
            begin
                @(posedge rx_par_valid);
                #1;
                if (!rx_frame_locked) begin
                    $display("FAIL: frame0 par_valid with frame_locked=0");
                    errors++;
                end
                for (int w = 0; w < WORDS; w++) begin
                    if (rx_par_data[w] !== frame0[w]) begin
                        $display("FAIL: frame0 word%0d got %02h want %02h",
                                 w, rx_par_data[w], frame0[w]);
                        errors++;
                    end
                end
                frames++;
            end
        join

        repeat (4) @(posedge clk_bit);

        fork
            send_frame(frame1);
            begin
                @(posedge rx_par_valid);
                #1;
                for (int w = 0; w < WORDS; w++) begin
                    if (rx_par_data[w] !== frame1[w]) begin
                        $display("FAIL: frame1 word%0d got %02h want %02h",
                                 w, rx_par_data[w], frame1[w]);
                        errors++;
                    end
                end
                frames++;
            end
        join

        repeat (4) @(posedge clk_bit);
        if (frames != 2) begin
            $display("FAIL: expected 2 aligned frames, got %0d", frames);
            errors++;
        end
        if (errors == 0)
            $display("PASS: tb_rx_half_rate DATA_WIDTH=%0d WORDS=%0d LSB=%0d frames=%0d",
                     DATA_WIDTH, WORDS, LSB_FIRST, frames);
        else
            $display("FAIL: tb_rx_half_rate errors=%0d", errors);
        $finish;
    end

    initial begin
        #200000;
        $display("FAIL: tb_rx_half_rate timeout (frames=%0d)", frames);
        $finish;
    end

endmodule
