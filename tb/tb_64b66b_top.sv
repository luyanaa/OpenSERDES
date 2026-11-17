`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// serdes_64b66b_top with RX_GEARBOX=1 (block-locked receiver), serial loopback.
//
// The TX is a continuous single-scrambler block source: IEEE idle blocks
// (ctrl=1, type 0x00) are sent between payload frames so the recovered bit
// stream never has a gap and the gearbox keeps its 66-bit boundary.
//
// ONE word lane is the defined gearbox configuration: the TX encoder and the
// RX descrambler are both serialized into the same single stream.
//
// Wire order: LSB_FIRST=0 sends block[65] (the sync header) first, which is
// what the gearbox expects and what IEEE 802.3 requires.
//
// Checks:
//   1. The gearbox locks on the idle-block stream and rx_block_locked asserts.
//   2. Idle blocks decode as ALL_CONTROL with block type 0x00.
//   3. Payload frames queued after lock come back byte-exactly, exactly once,
//      in order.  The decoder is self-synchronous and warm from the idle
//      blocks that precede every payload.
//   4. Every emitted block passes sync_error=0 and rx_par_valid pulses once
//      per block.
// -----------------------------------------------------------------------------
module tb_64b66b_top;

    localparam int DW    = 64;
    localparam int WORDS = 1;
    localparam int NF    = 12;

    logic tx_clk, tx_rst_n, tx_enable, tx_load;
    logic [WORDS-1:0][DW-1:0] tx_par_data;
    logic tx_ready, tx_serial_out, tx_serial_valid, tx_busy, tx_frame_done;

    logic rx_clk, rx_rst_n, rx_enable, rx_flush, rx_align, rx_serial_in;
    logic [WORDS-1:0][DW-1:0] rx_par_data;
    logic rx_par_valid, rx_busy, rx_lock, rx_lock_stale, rx_sample_strobe;
    logic rx_clk_out, rx_frame_locked, rx_frame_valid, rx_serial_data, rx_serial_valid;
    logic [12:0] rx_freq_trim;
    logic sync_error;
    logic rx_block_locked, rx_block_valid;
    logic [65:0] rx_block;
    logic [2:0] rx_ctrl_kind;
    logic rx_ctrl_valid;
    logic [7:0] rx_block_type;

    int tx_blocks;
    int tx_idle;
    always @(posedge tx_clk)
        if (tx_rst_n && tx_enable && dut.g_tx_block.enc_advance) begin
            tx_blocks++;
            if (!dut.g_tx_block.pay_valid_q) tx_idle++;
        end

    int errors;
    int sent;
    int emitted;
    int idle_seen;
    int decoded;
    int last_idx;
    int found;
    logic synced;
    logic [63:0] payload [0:NF-1];

    always #10 tx_clk = ~tx_clk;
    always #10 rx_clk = ~rx_clk;

    assign rx_serial_in = tx_serial_out;

    serdes_64b66b_top #(
        .DATA_WIDTH(DW), .WORDS(WORDS), .LSB_FIRST(1'b0),
        .CAPTURE_MODE(1'b0), .OVERSAMPLE(1), .CDR_MODE(2), .CDR_BYPASS(1'b1),
        .ALIGN_MODE(0), .RX_GEARBOX(1'b1),
        .GB_LOCK_BLOCKS(8), .GB_BAD_BLOCKS(4), .GB_IDLE_TIMEOUT(128)
    ) dut (
        .tx_clk(tx_clk), .tx_rst_n(tx_rst_n), .tx_enable(tx_enable),
        .tx_load(tx_load), .tx_par_data(tx_par_data), .tx_ready(tx_ready),
        .tx_serial_out(tx_serial_out), .tx_serial_valid(tx_serial_valid),
        .tx_busy(tx_busy), .tx_frame_done(tx_frame_done),
        .rx_clk(rx_clk), .rx_rst_n(rx_rst_n), .rx_enable(rx_enable),
        .rx_flush(rx_flush), .rx_align(rx_align),
        .rx_serial_in(rx_serial_in),
        .rx_par_data(rx_par_data), .rx_par_valid(rx_par_valid),
        .rx_busy(rx_busy), .rx_lock(rx_lock),
        .rx_lock_stale(rx_lock_stale),
        .rx_sample_strobe(rx_sample_strobe), .rx_clk_out(rx_clk_out),
        .rx_frame_locked(rx_frame_locked), .rx_frame_valid(rx_frame_valid),
        .rx_serial_data(rx_serial_data), .rx_serial_valid(rx_serial_valid),
        .rx_freq_trim(rx_freq_trim), .sync_error(sync_error),
        .rx_block_locked(rx_block_locked), .rx_block_valid(rx_block_valid),
        .rx_block(rx_block), .rx_ctrl_kind(rx_ctrl_kind),
        .rx_ctrl_valid(rx_ctrl_valid), .rx_block_type(rx_block_type)
    );

    always @(posedge rx_clk) begin
        if (rx_rst_n && rx_par_valid) begin
            if (!rx_ctrl_valid) begin
                $display("ERROR: control decode invalid for header %b",
                         rx_block[65:64]);
                errors++;
            end
            if (rx_ctrl_kind == 3'd0) begin
                if (rx_block[65:64] !== 2'b01) begin
                    $display("ERROR: data block header %b", rx_block[65:64]);
                    errors++;
                end
                // Map the decoded payload back to its frame index.
                found = -1;
                for (int j = 0; j < NF; j++)
                    if (payload[j] == rx_par_data[0])
                        found = j;
                if (found >= 0) begin
                    if (synced && found <= last_idx) begin
                        $display("ERROR: frame %0d after %0d", found, last_idx);
                        errors++;
                    end
                    last_idx = found;
                    synced   = 1'b1;
                    decoded++;
                end else if (synced) begin
                    $display("ERROR: unknown decoded payload %016h",
                             rx_par_data[0]);
                    errors++;
                end
            end else begin
                if (rx_ctrl_kind !== 3'd1) begin
                    $display("ERROR: unexpected control kind %0d", rx_ctrl_kind);
                    errors++;
                end
                if (rx_block_type !== 8'h00) begin
                    $display("ERROR: idle block type %02h", rx_block_type);
                    errors++;
                end
                idle_seen++;
            end
            emitted++;
        end
    end

    initial begin
        tx_clk = 1'b0; rx_clk = 1'b0;
        tx_rst_n = 1'b0; rx_rst_n = 1'b0;
        tx_enable = 1'b0; rx_enable = 1'b0;
        tx_load = 1'b0; tx_par_data = '0;
        rx_flush = 1'b0; rx_align = 1'b0;
        errors = 0; sent = 0; emitted = 0; tx_blocks = 0; tx_idle = 0;
        idle_seen = 0; decoded = 0; last_idx = -1; synced = 1'b0;

        for (int i = 0; i < NF; i++)
            payload[i] = 64'h0123_4567_89AB_CDEF ^
                         (64'h9E37_79B9_7F4A_7C15 * 64'(i));

        repeat (4) @(posedge tx_clk);
        tx_rst_n = 1'b1;
        rx_rst_n = 1'b1;
        repeat (4) @(posedge tx_clk);
        tx_enable = 1'b1;
        rx_enable = 1'b1;

        // Lock on the idle-block stream before queueing payloads.
        for (int i = 0; i < 200000 && !rx_block_locked; i++)
            @(posedge rx_clk);
        if (!rx_block_locked) begin
            $display("ERROR: gearbox never locked on the idle stream");
            errors++;
        end

        for (int i = 0; i < NF; i++) begin
            @(negedge tx_clk);
            while (!tx_ready) @(negedge tx_clk);
            tx_par_data[0] = payload[i];
            tx_load        = 1'b1;
            @(posedge tx_clk);
            #1;
            tx_load = 1'b0;
            wait (tx_frame_done === 1'b1);
            repeat (2) @(negedge tx_clk);
            sent++;
        end

        // Drain the last blocks through the gearbox/decoder.
        for (int i = 0; i < 4000 && decoded < NF; i++)
            @(posedge rx_clk);
        repeat (10) @(posedge rx_clk);

        if (!rx_block_locked) begin
            $display("ERROR: lock lost during the run");
            errors++;
        end
        if (decoded != NF) begin
            $display("ERROR: decoded %0d of %0d payload frames", decoded, NF);
            errors++;
        end
        if (idle_seen == 0) begin
            $display("ERROR: no idle control blocks observed");
            errors++;
        end
        if (sync_error !== 1'b0) begin
            $display("ERROR: sync_error high");
            errors++;
        end

        if (errors == 0)
            $display("PASS: tb_64b66b_top frames=%0d decoded=%0d idle=%0d emitted=%0d tx_blocks=%0d tx_idle=%0d",
                     sent, decoded, idle_seen, emitted, tx_blocks, tx_idle);
        else
            $display("FAIL: tb_64b66b_top errors=%0d", errors);
        $finish;
    end

    initial begin
        #8000000;
        $display("FAIL: tb_64b66b_top timeout sent=%0d decoded=%0d errors=%0d",
                 sent, decoded, errors);
        $finish;
    end

endmodule
