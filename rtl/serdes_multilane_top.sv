`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Multi-lane source-synchronous SERDES wrapper with lane-to-lane deskew.
//
// TX (tx_clk): one serializer_stream per lane.  All lanes are fed from the same
// word-slot schedule, so they transmit the same word position at the same time:
//
//   - a per-lane sync word right after enable (comma_align training pattern),
//   - one marker word every MARKER_PERIOD accepted payload words, with a
//     wrapping epoch in the low bits,
//   - an idle word whenever the payload source has nothing to send.
//
// The stream never has a bit gap while tx_enable is high, which is what keeps
// the receivers' 8-bit word counter and the deskew marker cadence stable.
//
// RX: each lane has its own recovered bit clock (rx_clk[l]) and a
// serdes_rx_digital in CDR_MODE=2 source-synchronous mode.  ALIGN_MODE=1 uses
// the SYNC_WORD comma pattern to establish the per-lane 8-bit word boundary and
// then strips the pattern.  Every accepted word is written to a per-lane
// async_fifo, which moves it into core_clk.
//
// Core (core_clk): lane_deskew absorbs the lane-to-lane word skew and releases
// the lanes in lockstep from the periodic marker boundary.  Markers and idle
// words are consumed, so rx_data/rx_valid carry payload words only: word n is
// the same payload word on every lane and in source order.
//
// Parameter constraints:
//   MARKER_PERIOD > MAX_SKEW (otherwise the deskew epoch match can slip)
//   DESKEW_DEPTH >= 2*MARKER_PERIOD + MAX_SKEW (power of two)
//   FIFO_DEPTH a power of two (async_fifo requirement)
//   DATA_WIDTH > TAG_WIDTH
//
// The serial line is a plain 1-bit stream per lane; encoding (8b/10b,
// 64b/66b) is a separate layer.
// -----------------------------------------------------------------------------
module serdes_multilane_top #(
    parameter int LANES         = 4,
    parameter int DATA_WIDTH    = 8,
    parameter int MARKER_PERIOD = 16,
    parameter int MAX_SKEW      = 8,
    parameter int DESKEW_DEPTH  = 64,
    parameter int FIFO_DEPTH    = 16,
    parameter int FIFO_SYNC     = 2,
    parameter int TAG_WIDTH     = 4,
    parameter logic [TAG_WIDTH-1:0] MARKER_TAG = 4'hA,
    parameter logic [TAG_WIDTH-1:0] IDLE_TAG   = 4'hF,
    parameter logic [DATA_WIDTH-1:0] SYNC_WORD  = 'hBC,
    // RX endpoint knobs.
    parameter int  OVERSAMPLE     = 1,
    parameter int  SYNC_STAGES    = 3,
    parameter int  CDR_MODE       = 2,
    parameter bit  CDR_BYPASS     = 1'b1,
    parameter int  ALIGN_MODE     = 1,
    parameter int  LOCK_THRESH    = 8,
    parameter int  LOCK_STALE_BITS= 16,
    parameter int  ADAPT_FRAC_BITS= 12,
    parameter int  ADAPT_LOOP_GAIN= 8,
    // Lane width negotiation.  0 (default) = fixed width: every lane must
    // frame or the link never comes up.  1 = negotiate: a lane that never
    // frames is dropped, the link runs narrower and publishes `lane_width`.
    // Never enabled by default: it is a protocol decision (PCIe negotiates
    // x1/x2/x4/x8, a bonded chiplet link must not silently lose a lane), so the
    // layer above opts in with WIDTH_MODE=1 and owns what it means.
    parameter bit  WIDTH_MODE             = 1'b0,
    parameter int  MIN_LANES              = 1,
    parameter int  LANE_UP_WINDOW         = 1024,
    parameter int  LANE_RESTORE_WINDOW    = 65536,
    // 0: a dropped lane returns only on `width_rescan`.
    parameter bit  AUTO_RESTORE           = 1'b0
) (
    // TX parallel side.
    input  logic                              tx_clk,
    input  logic                              tx_rst_n,
    input  logic                              tx_enable,
    input  logic                              tx_valid,
    output logic                              tx_ready,
    input  logic [LANES-1:0][DATA_WIDTH-1:0]  tx_data,
    output logic [LANES-1:0]                  tx_serial_out,
    // RX serial side: one recovered bit clock per lane.
    input  logic [LANES-1:0]                  rx_clk,
    input  logic                              rx_rst_n,
    input  logic                              rx_enable,
    input  logic                              rx_flush,
    input  logic [LANES-1:0]                  rx_serial_in,
    output logic [LANES-1:0]                  rx_frame_locked,
    // Deskewed payload side.
    input  logic                              core_clk,
    input  logic                              core_rst_n,
    input  logic                              core_enable,
    output logic [LANES-1:0][DATA_WIDTH-1:0]  rx_data,
    output logic                              rx_valid,
    input  logic                              rx_ready,
    output logic                              aligned,
    output logic                              marker_ok,
    output logic                              marker_error,
    output logic [9:0]                        realign_count,
    output logic [LANES-1:0]                  lane_locked,
    // Per-lane stickiness: a lane that had word framing and lost it.  Cleared
    // by rx_flush (or reset), not by a successful re-align, so a degraded lane
    // stays visible to the SoC.
    output logic [LANES-1:0]                  lane_fault,
    // Everything the bonding layer needs: deskew aligned, every lane in the
    // link framed, and no lane in the link has been lost.
    output logic                              link_ok,
    // Negotiated width (WIDTH_MODE=1).  `lane_width` is the number of lanes in
    // the link, `lane_mask` selects them; `width_degraded` means narrower than
    // the design, `width_error` means the width hit MIN_LANES and the protocol
    // should report a lower capability.
    output logic [LANES-1:0]                  lane_mask,
    output logic [31:0]                       lane_width,
    output logic                              width_degraded,
    output logic                              width_error,
    output logic                              width_change,
    // Re-evaluate the width now (protocol "rescan"); also re-arms dropped lanes.
    input  logic                              width_rescan
);
    localparam int EPOCH_W = DATA_WIDTH - TAG_WIDTH;

    generate
        if (DATA_WIDTH <= TAG_WIDTH) begin : g_bad_tag_width
            multilane_tag_width_must_be_less_than_data_width u_reject();
        end
        // A bang-bang / oversampling receiver needs a clock faster than the
        // bit rate; CDR_BYPASS would defeat it.
        if (CDR_MODE != 2 && (OVERSAMPLE < 4 || CDR_BYPASS)) begin : g_bad_cdr
            multilane_oversampled_cdr_needs_oversample_ge_4_and_no_bypass u_reject();
        end
    endgenerate

    // -------------------------------------------------------------------------
    // TX: shared slot schedule, per-lane serializer.
    // -------------------------------------------------------------------------
    logic [EPOCH_W-1:0] epoch_q;
    logic [7:0]         pay_cnt_q;
    logic               send_sync_q, send_marker_q;
    logic [LANES-1:0]   ser_ready;
    logic               ser_ready_all;

    wire [DATA_WIDTH-1:0] idle_word   = {IDLE_TAG, {(DATA_WIDTH-TAG_WIDTH){1'b0}}};
    wire [DATA_WIDTH-1:0] marker_word = {MARKER_TAG, epoch_q};
    wire [DATA_WIDTH-1:0] ctrl_word   = send_sync_q   ? SYNC_WORD :
                                        send_marker_q ? marker_word : idle_word;

    assign ser_ready_all = &ser_ready;
    assign tx_ready      = tx_enable & ser_ready_all &
                           ~send_sync_q & ~send_marker_q;

    always_ff @(posedge tx_clk or negedge tx_rst_n) begin
        if (!tx_rst_n) begin
            send_sync_q   <= 1'b1;
            send_marker_q <= 1'b0;
            epoch_q       <= '0;
            pay_cnt_q     <= '0;
        end else if (!tx_enable) begin
            send_sync_q   <= 1'b1;
            send_marker_q <= 1'b0;
            epoch_q       <= '0;
            pay_cnt_q     <= '0;
        end else if (ser_ready_all) begin
            if (send_sync_q) begin
                send_sync_q   <= 1'b0;
                send_marker_q <= 1'b1;      // marker immediately after sync
            end else if (send_marker_q) begin
                send_marker_q <= 1'b0;
                epoch_q       <= epoch_q + 1'b1;
                pay_cnt_q     <= '0;
            end else if (tx_valid) begin
                if (pay_cnt_q == 8'(MARKER_PERIOD - 1))
                    send_marker_q <= 1'b1;
                else
                    pay_cnt_q <= pay_cnt_q + 1'b1;
            end
        end
    end

    generate
        for (genvar l = 0; l < LANES; l++) begin : g_tx_lane
            wire [DATA_WIDTH-1:0] lane_word =
                (send_sync_q | send_marker_q) ? ctrl_word :
                (tx_valid ? tx_data[l] : idle_word);

            // A word is presented every cycle: sync, marker, payload or idle.
            /* verilator lint_off PINCONNECTEMPTY */
            serializer_stream #(
                .DATA_WIDTH(DATA_WIDTH), .WORDS(1), .LSB_FIRST(1'b1)
            ) u_ser (
                .clk(tx_clk), .rst_n(tx_rst_n), .enable(tx_enable),
                .in_valid(tx_enable), .in_ready(ser_ready[l]),
                .in_word(lane_word),
                .serial_out(tx_serial_out[l]), .serial_valid(),
                .busy(), .frame_done()
            );
            /* verilator lint_on PINCONNECTEMPTY */
        end
    endgenerate

    // -------------------------------------------------------------------------
    // RX: per-lane source-synchronous receiver + CDC FIFO.
    // -------------------------------------------------------------------------
    logic [LANES-1:0]                 fifo_rempty;
    logic [LANES-1:0]                 fifo_wfull;
    logic [LANES-1:0]                 fifo_rinc;
    logic [LANES-1:0][DATA_WIDTH-1:0] fifo_rdata;
    logic [LANES-1:0]                 frame_locked_l;
    logic [LANES-1:0][DATA_WIDTH-1:0] deskew_in_data;
    logic [LANES-1:0]                 deskew_in_valid;
    logic [LANES-1:0]                 deskew_in_sync;
    logic [LANES-1:0]                 deskew_in_ready;
    logic [LANES-1:0][DATA_WIDTH-1:0] deskew_out_data;

    generate
        for (genvar l = 0; l < LANES; l++) begin : g_rx_lane
            logic [0:0][DATA_WIDTH-1:0] rx_par;
            logic                       rx_par_valid_l;

            /* verilator lint_off PINCONNECTEMPTY */
            serdes_rx_digital #(
                .DATA_WIDTH(DATA_WIDTH), .WORDS(1), .LSB_FIRST(1'b1),
                .OVERSAMPLE(OVERSAMPLE), .SYNC_STAGES(SYNC_STAGES),
                .LOCK_THRESH(LOCK_THRESH),
                .LOCK_STALE_BITS(LOCK_STALE_BITS),
                .CDR_MODE(CDR_MODE), .CDR_BYPASS(CDR_BYPASS),
                .ALIGN_MODE(ALIGN_MODE),
                .SYNC_LEN(DATA_WIDTH), .SYNC_PATTERN(SYNC_WORD),
                .ADAPT_FRAC_BITS(ADAPT_FRAC_BITS),
                .ADAPT_LOOP_GAIN(ADAPT_LOOP_GAIN)
            ) u_rx (
                .rx_clk(rx_clk[l]), .rx_rst_n(rx_rst_n),
                .rx_enable(rx_enable), .rx_flush(rx_flush),
                .rx_align(1'b0),
                .rx_serial_in(rx_serial_in[l]),
                .phase_offset('0), .rx_smp(1'b0), .rx_dll_tap(),
                .rx_par_data(rx_par), .rx_par_valid(rx_par_valid_l),
                .rx_busy(), .rx_lock(),
                .rx_lock_stale(),
                .rx_sample_strobe(),
                .rx_clk_out(),
                .rx_frame_locked(frame_locked_l[l]),
                .rx_frame_valid(),
                .rx_serial_data(),
                .rx_serial_valid(),
                .rx_freq_trim()
            );
            /* verilator lint_on PINCONNECTEMPTY */

            assign rx_frame_locked[l] = frame_locked_l[l];

            // Only framed words enter the elastic FIFO; the sync word itself
            // is consumed by comma_align.
            async_fifo #(
                .DATA_WIDTH(DATA_WIDTH), .DEPTH(FIFO_DEPTH),
                .SYNC_STAGES(FIFO_SYNC)
            ) u_fifo (
                .wclk(rx_clk[l]), .wrst_n(rx_rst_n),
                .winc(rx_par_valid_l & frame_locked_l[l]),
                .wdata(rx_par[0]), .wfull(fifo_wfull[l]),
                .rclk(core_clk), .rrst_n(core_rst_n),
                .rinc(fifo_rinc[l]), .rdata(fifo_rdata[l]),
                .rempty(fifo_rempty[l])
            );

            assign deskew_in_data[l]  = fifo_rdata[l];
            assign deskew_in_valid[l] = ~fifo_rempty[l];
            assign fifo_rinc[l]       = ~fifo_rempty[l] & deskew_in_ready[l];

            // The word-framing lock crosses into core_clk as a status level.
            cdc_sync #(.WIDTH(1), .STAGES(2)) u_lock_sync (
                .clk(core_clk), .rst_n(core_rst_n),
                .d(frame_locked_l[l]), .q(deskew_in_sync[l])
            );
        end
    endgenerate

    // -------------------------------------------------------------------------
    // Lane width negotiation.
    //
    // Liveness is "this lane is still delivering words", not the framing lock:
    // `deskew_in_sync` (rx_frame_locked) is sticky and would report a gone lane
    // as healthy forever.  The TX never gaps a lane, so a live lane always has
    // traffic and a dead one has none.
    // WIDTH_MODE=0 makes the negotiator a constant all-ones mask, i.e. exactly
    // the behaviour this top had before the parameter existed.
    // -------------------------------------------------------------------------
    logic [LANES-1:0] lane_activity;
    generate
        for (genvar l = 0; l < LANES; l++) begin : g_activity
            assign lane_activity[l] = deskew_in_valid[l] & deskew_in_ready[l];
        end
    endgenerate

    lane_width_negotiator #(
        .LANES(LANES), .MIN_LANES(MIN_LANES),
        .LANE_UP_WINDOW(LANE_UP_WINDOW),
        .LANE_RESTORE_WINDOW(LANE_RESTORE_WINDOW),
        .AUTO_RESTORE(AUTO_RESTORE), .WIDTH_MODE(WIDTH_MODE)
    ) u_width (
        .clk(core_clk), .rst_n(core_rst_n), .enable(core_enable),
        .clear(rx_flush), .rescan(width_rescan),
        .lane_ok(lane_activity),
        .lane_mask(lane_mask), .lane_dropped(), .lane_restored(),
        .width(lane_width), .degraded(width_degraded),
        .width_change(width_change), .width_error(width_error),
        .width_change_count()
    );

    // -------------------------------------------------------------------------
    // Lane health.
    // -------------------------------------------------------------------------
    logic [LANES-1:0] lane_ever_q;
    logic [LANES-1:0] lane_fault_q;

    always_ff @(posedge core_clk or negedge core_rst_n) begin
        if (!core_rst_n) begin
            lane_ever_q  <= '0;
            lane_fault_q <= '0;
        end else if (rx_flush | !core_enable) begin
            lane_ever_q  <= '0;
            lane_fault_q <= '0;
        end else begin
            lane_ever_q <= lane_ever_q | deskew_in_sync;
            // Framing that was established and then dropped is a fault; a lane
            // that never framed is simply not up yet.
            lane_fault_q <= lane_fault_q |
                            (lane_ever_q & ~deskew_in_sync);
        end
    end

    assign lane_fault = lane_fault_q;
    // A lane outside the negotiated mask is not expected to be locked and is
    // not a fault: the link is only "ok" over the lanes it actually has.
    assign link_ok    = aligned & (&(lane_locked | ~lane_mask))
                        & ~(|(lane_fault_q & lane_mask));

    lane_deskew #(
        .LANES(LANES), .DATA_WIDTH(DATA_WIDTH),
        .DEPTH(DESKEW_DEPTH), .MAX_SKEW(MAX_SKEW),
        .MARKER_PERIOD(MARKER_PERIOD), .TAG_WIDTH(TAG_WIDTH),
        .MARKER_TAG(MARKER_TAG), .IDLE_TAG(IDLE_TAG)
    ) u_deskew (
        .clk(core_clk), .rst_n(core_rst_n), .enable(core_enable),
        .lane_mask(lane_mask),
        .in_data(deskew_in_data), .in_valid(deskew_in_valid),
        .in_sync(deskew_in_sync), .in_ready(deskew_in_ready),
        .out_data(deskew_out_data), .out_valid(rx_valid),
        .out_ready(rx_ready),
        .aligned(aligned), .marker_ok(marker_ok),
        .marker_error(marker_error), .realign_count(realign_count),
        .lane_locked(lane_locked)
    );

    assign rx_data = deskew_out_data;

    /* verilator lint_off UNUSEDSIGNAL */
    logic [LANES-1:0] unused_fifo_full;
    assign unused_fifo_full = fifo_wfull;
    /* verilator lint_on UNUSEDSIGNAL */

endmodule
