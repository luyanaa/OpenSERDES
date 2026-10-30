`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Digital link test: TX -> channel -> RX -> frame scoreboard.
//
// This test uses two independent endpoint wrappers:
//   - TX serializes 5 words: a sync/comma word (0xBC) followed by 4 payload
//     bytes.
//   - RX comma-aligns on 0xBC, discards it, and captures the 4 payload bytes.
//
// Two independent scoreboards are used:
//   1. recovered serial bits (rx_serial_data on rx_serial_valid) at par_valid;
//   2. parallel rx_par_data at par_valid.
//
// The test also exercises per-frame re-alignment, a tx/rx gap (the serializer
// has one idle cycle between frames), CDR lock, and rx_flush recovery.
// -----------------------------------------------------------------------------
module tb_serdes_channel;

    localparam int DW       = 8;
    localparam int TX_WORDS = 5;   // sync word + 4 payload words
    localparam int RX_WORDS = 4;   // payload only
    localparam int RATIO    = DW * RX_WORDS;
    localparam int OS       = 4;

    logic tx_clk;
    logic rx_clk;
    logic tx_rst_n;
    logic rx_rst_n;
    logic tx_enable;
    logic tx_load;
    logic [TX_WORDS-1:0][DW-1:0] tx_data;

    logic tx_ready;
    logic tx_serial_out;
    logic tx_serial_valid;
    logic tx_busy;
    logic tx_frame_done;

    logic rx_enable;
    logic rx_flush;
    logic rx_align;
    logic [RX_WORDS-1:0][DW-1:0] rx_data;
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
    logic signed [12:0] rx_freq_trim;

    logic channel_out;
    logic channel_valid;

    int errors;
    int par_valid_count;
    logic [31:0] recovered_shift;
    logic [31:0] expected_serial;
    logic [31:0] expected_payload;
    logic [7:0]  expected_tag;
    logic        check_enable;
    int          frame_seen;

    always #5 rx_clk = ~rx_clk;   // 100 MHz oversampling clock
    always #20 tx_clk = ~tx_clk;  // 25 MHz bit clock (4x oversampling)

    serdes_tx_digital #(
        .DATA_WIDTH(DW),
        .WORDS(TX_WORDS),
        .LSB_FIRST(1'b1),
        .CAPTURE_MODE(1'b0)
    ) u_tx (
        .tx_clk(tx_clk),
        .tx_rst_n(tx_rst_n),
        .tx_enable(tx_enable),
        .tx_load(tx_load),
        .tx_par_data(tx_data),
        .tx_ready(tx_ready),
        .tx_serial_out(tx_serial_out),
        .tx_serial_valid(tx_serial_valid),
        .tx_busy(tx_busy),
        .tx_frame_done(tx_frame_done)
    );

    serdes_rx_digital #(
        .DATA_WIDTH(DW),
        .WORDS(RX_WORDS),
        .LSB_FIRST(1'b1),
        .OVERSAMPLE(OS),
        .SYNC_STAGES(3),
        .LOCK_THRESH(8),
        .LOCK_STALE_BITS(16),
        .CDR_MODE(0),
        .ALIGN_MODE(2),
        .SYNC_LEN(8),
        .SYNC_PATTERN(8'hBC)
    ) u_rx (
        .rx_clk(rx_clk),
        .rx_rst_n(rx_rst_n),
        .rx_enable(rx_enable),
        .rx_flush(rx_flush),
        .rx_align(rx_align),
        .rx_serial_in(channel_out),
        .phase_offset('0),
        .rx_par_data(rx_data),
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

    channel_model #(
        .DELAY_CYCLES(0),
        .BIT_ERROR_PERIOD(0),
        .INVERT(1'b0)
    ) channel (
        .clk(tx_clk),
        .rst_n(tx_rst_n),
        .serial_in(tx_serial_out),
        .serial_valid(tx_serial_valid),
        .serial_out(channel_out),
        .serial_valid_out(channel_valid)
    );

    always @(posedge rx_clk) begin
        if (rx_frame_valid) begin
            par_valid_count <= par_valid_count + 1;
            // Compare the last RATIO recovered serial bits.  rx_par_valid is
            // asserted in the cycle after the last bit was captured, so the
            // shift register still holds exactly that frame.  Frames from the
            // training phase are ignored by their tag.
            if (check_enable && (rx_data[0] == expected_tag)) begin
                if (recovered_shift !== expected_serial) begin
                    $display("ERROR: recovered serial mismatch: got %h expected %h",
                             recovered_shift, expected_serial);
                    errors <= errors + 1;
                end
                if ({rx_data[3], rx_data[2], rx_data[1], rx_data[0]}
                    !== expected_payload) begin
                    $display("ERROR: parallel frame mismatch: got %h expected %h",
                             {rx_data[3], rx_data[2], rx_data[1], rx_data[0]},
                             expected_payload);
                    errors <= errors + 1;
                end
                if (!rx_frame_locked) begin
                    $display("ERROR: par_valid without frame_locked");
                    errors <= errors + 1;
                end
                frame_seen <= 1;
            end
            recovered_shift <= '0;
        end else if (rx_serial_valid) begin
            recovered_shift <= {recovered_shift[RATIO-2:0], rx_serial_data};
        end
    end

    function automatic logic [RATIO-1:0] make_serial(input logic [RATIO-1:0] payload);
        for (int i = 0; i < RATIO; i++)
            make_serial[RATIO-1-i] = payload[i];
    endfunction

    task automatic load_tx_frame(input logic [TX_WORDS*DW-1:0] frame);
        begin
            while (!tx_ready) @(negedge tx_clk);
            @(negedge tx_clk);
            for (int w = 0; w < TX_WORDS; w++)
                tx_data[w] = frame[w*DW +: DW];
            tx_load = 1'b1;
            @(posedge tx_clk);
            #1;
            tx_load = 1'b0;
        end
    endtask

    task automatic wait_par_valid(input int timeout);
        int t;
        begin
            t = 0;
            while (!rx_par_valid && t < timeout) begin
                @(posedge rx_clk);
                #1;
                t++;
            end
            if (!rx_par_valid) begin
                $display("ERROR: timeout waiting for rx_par_valid");
                errors++;
            end
        end
    endtask

    task automatic wait_lock(input int timeout);
        int t;
        begin
            t = 0;
            while ((!rx_lock || !rx_frame_locked) && t < timeout) begin
                @(posedge rx_clk);
                #1;
                t++;
            end
            if (!rx_lock) begin
                $display("ERROR: CDR did not lock");
                errors++;
            end
            if (!rx_frame_locked) begin
                $display("ERROR: comma alignment did not lock");
                errors++;
            end
        end
    endtask

    task automatic send_training(input int n_frames);
        logic [TX_WORDS*DW-1:0] frame;
        begin
            frame = {{4{8'hAA}}, 8'hBC};
            for (int n = 0; n < n_frames; n++)
                load_tx_frame(frame);
        end
    endtask

    task automatic send_payload(input logic [RATIO-1:0] payload);
        logic [TX_WORDS*DW-1:0] frame;
        int t;
        begin
            frame = {payload, 8'hBC};
            expected_payload = payload;
            expected_serial  = make_serial(payload);
            expected_tag     = payload[7:0];
            frame_seen       = 0;
            check_enable     = 1'b1;
            load_tx_frame(frame);
            t = 0;
            while (!frame_seen && t < 20000) begin
                @(posedge rx_clk);
                #1;
                t++;
            end
            if (!frame_seen) begin
                $display("ERROR: timeout waiting for payload tag %h", expected_tag);
                errors++;
            end
            check_enable = 1'b0;
        end
    endtask

    initial begin
        tx_clk          = 1'b0;
        rx_clk          = 1'b0;
        tx_rst_n        = 1'b0;
        rx_rst_n        = 1'b0;
        tx_enable       = 1'b0;
        tx_load         = 1'b0;
        rx_enable       = 1'b0;
        rx_flush        = 1'b0;
        rx_align        = 1'b0;
        tx_data         = '0;
        errors          = 0;
        par_valid_count = 0;
        recovered_shift = '0;
        check_enable    = 1'b0;

        repeat (4) @(posedge rx_clk);
        tx_rst_n = 1'b1;
        rx_rst_n = 1'b1;
        wait (u_tx.tx_rst_n_sync);
        wait (u_rx.rx_rst_n_sync);
        repeat (2) @(posedge tx_clk);
        repeat (2) @(posedge rx_clk);
        tx_enable = 1'b1;
        rx_enable = 1'b1;

        // Training frames give the CDR edges and the comma detector a sync
        // word.  They also cover the serializer's inter-frame idle cycle.
        send_training(8);
        wait_lock(20000);

        // Stale training frames are rejected by their 0xAA tag.
        send_payload(32'h0123_4501);
        send_payload(32'h89AB_CD02);
        send_payload(32'hA5A5_5A03);
        send_payload(32'h0F0F_F004);

        // Flush and prove the link can re-acquire without a reset.
        @(negedge rx_clk);
        rx_flush = 1'b1;
        @(posedge rx_clk);
        #1;
        rx_flush = 1'b0;
        repeat (4) @(posedge rx_clk);
        if (rx_frame_locked) begin
            $display("ERROR: frame_locked survived rx_flush");
            errors++;
        end

        send_training(8);
        wait_lock(20000);
        send_payload(32'hCAFE_F005);

        if (par_valid_count < 5) begin
            $display("ERROR: too few par_valid pulses: %0d", par_valid_count);
            errors++;
        end

        if (errors != 0) begin
            $display("FAIL: tb_serdes_channel errors=%0d par_valid_count=%0d",
                     errors, par_valid_count);
            $fatal(1);
        end
        $display("PASS: tb_serdes_channel par_valid_count=%0d", par_valid_count);
        $finish;
    end

endmodule
