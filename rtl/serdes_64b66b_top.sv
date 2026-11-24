`timescale 1ns/1ps

// 64b/66b link wrapper.
//
// Payload words are 64 bits and are encoded into 66-bit PCS blocks before
// serialization; the RX core deserializes 66-bit blocks and the decoder
// reconstructs 64-bit payload words.
//
// Two RX datapaths are available:
//
//   RX_GEARBOX=0 (parallel): the RX core deserializes one 66-bit block per word
//     lane and each lane has its own descrambler.  The 66-bit frame-sync
//     pattern (ALIGN_MODE) locates the block boundary.
//
//   RX_GEARBOX=1 (block locked): the RX core is reduced to a 1-bit CDR/sampler,
//     pcs_64b66b_rx_gearbox finds the 66-bit block boundary directly on the
//     recovered bit stream (no sync pattern required) and
//     pcs_64b66b_ctrl_decode classifies the descrambled control blocks.  The TX
//     side becomes a continuous block source: one encoder emits payload blocks
//     and IEEE idle blocks (ctrl=1, block type 0x00) back-to-back, so the
//     single self-synchronous scrambler stream never has a bit gap.  Idle
//     blocks are what keep the receiver locked between payload frames.
//
// The gearbox configuration is defined for one word lane (WORDS==1): the
// continuous stream is descrambled by a single decoder, so the TX must not
// interleave independently scrambled lanes.
//
// Wire order: IEEE 802.3 sends the 2-bit sync header first, which is
// LSB_FIRST=0 for this serializer.
//
// rx_par_data[0] / rx_par_valid carry one 64-bit payload per accepted block
// (idle blocks included); rx_ctrl_kind tells data blocks from control blocks.
//
// The PCS primitive covers data and control-header blocks; it does not
// implement full IEEE control-field/lane-distribution semantics.
module serdes_64b66b_top #(
    parameter int DATA_WIDTH      = 64,
    parameter int WORDS           = 8,
    parameter bit LSB_FIRST       = 1'b1,
    parameter bit CAPTURE_MODE    = 1'b0,
    parameter int OVERSAMPLE      = 1,
    parameter int SYNC_STAGES     = 3,
    parameter int LOCK_THRESH     = 8,
    parameter int LOCK_STALE_BITS = 16,
    parameter int CDR_MODE        = 2,
    parameter bit CDR_BYPASS      = 1'b1,
    parameter int ALIGN_MODE      = 0,
    parameter int SYNC_LEN        = 8,
    parameter logic [SYNC_LEN-1:0] SYNC_PATTERN = 'hBC,
    parameter int ADAPT_FRAC_BITS = 12,
    parameter int ADAPT_LOOP_GAIN = 8,
    // Block-locked RX: 1-bit CDR + 66-bit sliding-window gearbox.
    parameter bit RX_GEARBOX      = 1'b0,
    parameter int GB_LOCK_BLOCKS  = 8,
    parameter int GB_BAD_BLOCKS   = 4,
    parameter int GB_IDLE_TIMEOUT = 128
) (
    input  logic                              tx_clk,
    input  logic                              tx_rst_n,
    input  logic                              tx_enable,
    input  logic                              tx_load,
    input  logic [WORDS-1:0][DATA_WIDTH-1:0]  tx_par_data,
    output logic                              tx_ready,
    output logic                              tx_serial_out,
    output logic                              tx_serial_valid,
    output logic                              tx_busy,
    output logic                              tx_frame_done,

    input  logic                              rx_clk,
    input  logic                              rx_rst_n,
    input  logic                              rx_enable,
    input  logic                              rx_flush,
    input  logic                              rx_align,
    input  logic                              rx_serial_in,
    output logic [WORDS-1:0][DATA_WIDTH-1:0]  rx_par_data,
    output logic                              rx_par_valid,
    output logic                              rx_busy,
    output logic                              rx_lock,
    output logic                              rx_lock_stale,
    output logic                              rx_sample_strobe,
    output logic                              rx_clk_out,
    output logic                              rx_frame_locked,
    output logic                              rx_frame_valid,
    output logic                              rx_serial_data,
    output logic                              rx_serial_valid,
    // Two's-complement value; not declared signed for Yosys/OpenROAD portability.
    output logic [ADAPT_FRAC_BITS:0]   rx_freq_trim,
    output logic                              sync_error,
    // RX_GEARBOX=1 status.  rx_block is the raw locked 66-bit block;
    // rx_ctrl_* decode the descrambled block payload (valid when
    // rx_ctrl_valid); rx_par_data[0] is the descrambled 64-bit payload.
    output logic                              rx_block_locked,
    output logic                              rx_block_valid,
    output logic [65:0]                       rx_block,
    output logic [2:0]                        rx_ctrl_kind,
    output logic                              rx_ctrl_valid,
    output logic [7:0]                        rx_block_type
);
    localparam int LINE_WIDTH = 66;
    localparam logic [63:0] IDLE_DATA = 64'h0;

    generate
        // Fail closed: this wrapper has no DLL phase mux/sampler, so it cannot
        // supply rx_smp or accept rx_dll_tap.  The unresolved module is an
        // elaboration error in both simulation and synthesis.
        if (CDR_MODE == 4) begin : g_mode4_needs_external_sampler
            dll_mode4_requires_an_external_sampler u_reject();
        end
        // The block-locked receiver descrambles the single serial stream.  The
        // parallel TX scrambles one independent lane per word, which a single
        // descrambler cannot recover from the interleaved stream.
        if (RX_GEARBOX && (WORDS != 1)) begin : g_gearbox_needs_one_lane
            rx_gearbox_mode_supports_only_one_word_lane u_reject();
        end
    endgenerate

    logic [WORDS-1:0][LINE_WIDTH-1:0] tx_line;
    logic [WORDS-1:0][LINE_WIDTH-1:0] rx_line;
    logic [WORDS-1:0] tx_sync_ok;
    logic [WORDS-1:0] rx_sync_ok;

    // -------------------------------------------------------------------------
    // TX datapath.
    // -------------------------------------------------------------------------
    generate
        if (RX_GEARBOX) begin : g_tx_block
            logic [63:0] pay_q;
            logic        pay_valid_q, sending_pay_q;
            logic        ser_ready, ser_done, ser_busy;
            logic [63:0] enc_data;
            logic        enc_ctrl;
            logic [65:0] enc_block;
            logic        enc_advance;

            // One encoder for the whole stream: payload when one is pending,
            // otherwise an IEEE all-control idle block.  Advancing once per
            // committed block keeps the scrambler continuous.
            assign enc_data    = pay_valid_q ? pay_q : IDLE_DATA;
            assign enc_ctrl    = ~pay_valid_q;
            assign enc_advance = ser_ready;   // in_valid is tied high

            /* verilator lint_off PINCONNECTEMPTY */
            pcs_64b66b_encoder u_enc (
                .clk(tx_clk), .rst_n(tx_rst_n), .advance(enc_advance),
                .data_in(enc_data), .ctrl(enc_ctrl),
                .block_out(enc_block), .sync_ok()
            );
            /* verilator lint_on PINCONNECTEMPTY */
            assign tx_sync_ok = '1;

            serializer_stream #(
                .DATA_WIDTH(LINE_WIDTH), .WORDS(1), .LSB_FIRST(LSB_FIRST)
            ) u_ser (
                .clk(tx_clk), .rst_n(tx_rst_n), .enable(tx_enable),
                .in_valid(1'b1), .in_ready(ser_ready), .in_word(enc_block),
                .serial_out(tx_serial_out), .serial_valid(tx_serial_valid),
                .busy(ser_busy), .frame_done(ser_done)
            );

            assign tx_ready      = ~pay_valid_q;
            assign tx_busy       = ser_busy | pay_valid_q;
            assign tx_frame_done = ser_done & sending_pay_q;

            always_ff @(posedge tx_clk or negedge tx_rst_n) begin
                if (!tx_rst_n) begin
                    pay_q         <= '0;
                    pay_valid_q   <= 1'b0;
                    sending_pay_q <= 1'b0;
                end else if (!tx_enable) begin
                    pay_valid_q   <= 1'b0;
                    sending_pay_q <= 1'b0;
                end else begin
                    if (tx_load && tx_ready) begin
                        pay_q       <= tx_par_data[0];
                        pay_valid_q <= 1'b1;
                    end else if (enc_advance && pay_valid_q) begin
                        pay_valid_q <= 1'b0;
                    end

                    if (enc_advance && pay_valid_q)
                        sending_pay_q <= 1'b1;
                    else if (ser_done)
                        sending_pay_q <= 1'b0;
                end
            end
        end else begin : g_tx_frame
            for (genvar w = 0; w < WORDS; w++) begin : g_tx_enc
                // Scrambler state persists per lane; advance on each new
                // payload (tx_load).
                pcs_64b66b_encoder u_enc (
                    .clk(tx_clk), .rst_n(tx_rst_n), .advance(tx_load),
                    .data_in(tx_par_data[w]), .ctrl(1'b0),
                    .block_out(tx_line[w]), .sync_ok(tx_sync_ok[w])
                );
            end

            serdes_tx_digital #(
                .DATA_WIDTH(LINE_WIDTH), .WORDS(WORDS),
                .LSB_FIRST(LSB_FIRST), .CAPTURE_MODE(CAPTURE_MODE)
            ) u_tx (
                .tx_clk(tx_clk), .tx_rst_n(tx_rst_n), .tx_enable(tx_enable),
                .tx_load(tx_load), .tx_par_data(tx_line), .tx_ready(tx_ready),
                .tx_serial_out(tx_serial_out),
                .tx_serial_valid(tx_serial_valid),
                .tx_busy(tx_busy), .tx_frame_done(tx_frame_done)
            );
        end
    endgenerate

    // -------------------------------------------------------------------------
    // RX datapath.
    // -------------------------------------------------------------------------
    generate
        if (RX_GEARBOX) begin : g_rx_gearbox
            // 1-bit receiver: the CDR/sampler recovers the bit clock, the
            // gearbox owns the block boundary.  ALIGN_MODE=0: every bit is a
            // one-bit "frame" so the deserializer just registers the bit.
            logic [0:0][0:0] rx_bit;
            logic            rx_bit_valid;
            logic [65:0]     gb_block;
            logic            gb_blk_valid, gb_locked, gb_overflow;
            logic [63:0]     gb_word;
            logic            gb_word_valid;
            logic [63:0]     dec_data;
            logic            dec_sync_ok, dec_ctrl_block;
            logic [65:0]     dec_block;
            // Link-health watchdog outputs: this wrapper exposes no
            // link-health port, so they are intentionally left unused.
            /* verilator lint_off UNUSEDSIGNAL */
            logic            comma_lock_timeout_unused, comma_lock_stuck_unused;
            /* verilator lint_on UNUSEDSIGNAL */

            serdes_rx_digital #(
                .DATA_WIDTH(1), .WORDS(1), .LSB_FIRST(1'b1),
                .OVERSAMPLE(OVERSAMPLE), .SYNC_STAGES(SYNC_STAGES),
                .LOCK_THRESH(LOCK_THRESH), .LOCK_STALE_BITS(LOCK_STALE_BITS),
                .CDR_MODE(CDR_MODE), .CDR_BYPASS(CDR_BYPASS),
                .ALIGN_MODE(0),
                .ADAPT_FRAC_BITS(ADAPT_FRAC_BITS),
                .ADAPT_LOOP_GAIN(ADAPT_LOOP_GAIN)
            ) u_rx (
                .rx_clk(rx_clk), .rx_rst_n(rx_rst_n), .rx_enable(rx_enable),
                .rx_flush(rx_flush), .rx_align(1'b0),
                .rx_serial_in(rx_serial_in),
                .phase_offset('0), .rx_smp(1'b0),
                /* verilator lint_off PINCONNECTEMPTY */
                .rx_dll_tap(),
                /* verilator lint_on PINCONNECTEMPTY */
                .rx_par_data(rx_bit), .rx_par_valid(rx_bit_valid),
                .rx_busy(rx_busy), .rx_lock(rx_lock),
                .rx_lock_stale(rx_lock_stale),
                .rx_sample_strobe(rx_sample_strobe), .rx_clk_out(rx_clk_out),
                .rx_frame_locked(rx_frame_locked),
                .rx_frame_valid(rx_frame_valid),
                .rx_serial_data(rx_serial_data),
                .rx_serial_valid(rx_serial_valid),
                .rx_freq_trim(rx_freq_trim),
                .comma_lock_timeout(comma_lock_timeout_unused),
                .comma_lock_stuck(comma_lock_stuck_unused)
            );

            pcs_64b66b_rx_gearbox #(
                .LOCK_BLOCKS(GB_LOCK_BLOCKS), .BAD_BLOCKS(GB_BAD_BLOCKS),
                .IDLE_TIMEOUT(GB_IDLE_TIMEOUT)
            ) u_gb (
                .clk(rx_clk), .rst_n(rx_rst_n), .enable(rx_enable),
                .slip_req(rx_flush),
                .serial_in(rx_bit[0][0]), .serial_valid(rx_bit_valid),
                .block_out(gb_block), .blk_valid(gb_blk_valid),
                .blk_locked(gb_locked),
                .out_data(gb_word), .out_valid(gb_word_valid),
                .out_ready(1'b1), .overflow(gb_overflow)
            );

            // One descrambler pass per locked block; the self-synchronous
            // scrambler re-converges after a relock without a seed.
            pcs_64b66b_decoder u_dec (
                .clk(rx_clk), .rst_n(rx_rst_n), .advance(gb_blk_valid),
                .block_in(gb_block), .data_out(dec_data),
                .sync_ok(dec_sync_ok), .ctrl_block(dec_ctrl_block)
            );

            // Control fields live in the descrambled payload, not on the wire.
            assign dec_block = {gb_block[65:64], dec_data};

            /* verilator lint_off PINCONNECTEMPTY */
            pcs_64b66b_ctrl_decode u_ctrl (
                .block_in(dec_block), .kind(rx_ctrl_kind),
                .byte_ctrl(), .data(),
                .block_type(rx_block_type),
                .ctrl_field(), .field_valid(), .type_valid(rx_ctrl_valid)
            );
            /* verilator lint_on PINCONNECTEMPTY */

            for (genvar w = 0; w < WORDS; w++) begin : g_rx_word
                if (w == 0) begin : g_first_word
                    assign rx_par_data[w] = DATA_WIDTH'(dec_data);
                end else begin : g_other_word
                    assign rx_par_data[w] = '0;
                end
            end

            assign rx_par_valid     = gb_blk_valid;
            assign rx_block         = gb_block;
            assign rx_block_valid   = gb_blk_valid;
            assign rx_block_locked  = gb_locked;
            assign rx_sync_ok       = {{(WORDS-1){1'b1}},
                                       dec_sync_ok & ~gb_overflow};
        end else begin : g_rx_lane
            // Parallel receiver: the RX core deserializes one 66-bit block per
            // word lane and each lane has its own descrambler.
            logic [WORDS-1:0][DATA_WIDTH-1:0] dec_data;
            logic [WORDS-1:0] dec_sync_ok;
            // Per-lane ctrl/block-kind output is not used in parallel-lane
            // mode (block framing is handled by the gearbox path).
            /* verilator lint_off UNUSEDSIGNAL */
            logic [WORDS-1:0] dec_ctrl;
            /* verilator lint_on UNUSEDSIGNAL */
            // Link-health watchdog outputs: this wrapper exposes no
            // link-health port, so they are intentionally left unused.
            /* verilator lint_off UNUSEDSIGNAL */
            logic comma_lock_timeout_unused, comma_lock_stuck_unused;
            /* verilator lint_on UNUSEDSIGNAL */

            serdes_rx_digital #(
                .DATA_WIDTH(LINE_WIDTH), .WORDS(WORDS),
                .LSB_FIRST(LSB_FIRST),
                .OVERSAMPLE(OVERSAMPLE), .SYNC_STAGES(SYNC_STAGES),
                .LOCK_THRESH(LOCK_THRESH), .LOCK_STALE_BITS(LOCK_STALE_BITS),
                .CDR_MODE(CDR_MODE), .CDR_BYPASS(CDR_BYPASS),
                .ALIGN_MODE(ALIGN_MODE),
                .SYNC_LEN(SYNC_LEN), .SYNC_PATTERN(SYNC_PATTERN),
                .ADAPT_FRAC_BITS(ADAPT_FRAC_BITS),
                .ADAPT_LOOP_GAIN(ADAPT_LOOP_GAIN)
            ) u_rx (
                .rx_clk(rx_clk), .rx_rst_n(rx_rst_n), .rx_enable(rx_enable),
                .rx_flush(rx_flush), .rx_align(rx_align),
                .rx_serial_in(rx_serial_in),
                .phase_offset('0), .rx_smp(1'b0),
                /* verilator lint_off PINCONNECTEMPTY */
                .rx_dll_tap(),
                /* verilator lint_on PINCONNECTEMPTY */
                .rx_par_data(rx_line), .rx_par_valid(rx_par_valid),
                .rx_busy(rx_busy), .rx_lock(rx_lock),
                .rx_lock_stale(rx_lock_stale),
                .rx_sample_strobe(rx_sample_strobe), .rx_clk_out(rx_clk_out),
                .rx_frame_locked(rx_frame_locked),
                .rx_frame_valid(rx_frame_valid),
                .rx_serial_data(rx_serial_data),
                .rx_serial_valid(rx_serial_valid),
                .rx_freq_trim(rx_freq_trim),
                .comma_lock_timeout(comma_lock_timeout_unused),
                .comma_lock_stuck(comma_lock_stuck_unused)
            );

            for (genvar w = 0; w < WORDS; w++) begin : g_rx_dec
                pcs_64b66b_decoder u_dec (
                    .clk(rx_clk), .rst_n(rx_rst_n), .advance(rx_par_valid),
                    .block_in(rx_line[w]), .data_out(dec_data[w]),
                    .sync_ok(dec_sync_ok[w]), .ctrl_block(dec_ctrl[w])
                );
            end

            assign rx_par_data      = dec_data;
            assign rx_block         = '0;
            assign rx_block_valid   = rx_par_valid;
            assign rx_block_locked  = 1'b1;
            assign rx_ctrl_kind     = 3'd0;
            assign rx_ctrl_valid    = 1'b0;
            assign rx_block_type    = 8'h01;
            assign rx_sync_ok       = dec_sync_ok;
        end
    endgenerate

    assign sync_error = ~(&rx_sync_ok) | ~(&tx_sync_ok);
endmodule
