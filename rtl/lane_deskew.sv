`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Lane-to-lane deskew with periodic alignment markers.
//
// A multi-lane link carries one word stream per lane.  All lanes are aligned at
// the transmitter, but the receiver sees them at different times: channel skew,
// per-lane CDR lock time and per-lane elastic-FIFO latency all add a whole-word
// offset.  The transmitter inserts one marker word on every lane after every
// MARKER_PERIOD payload words and bumps the marker epoch; this module buffers
// each lane independently and releases the lanes in lockstep from the marker
// boundary, so word n of every lane appears on the same output beat.
//
// Word classes are reserved ordered sets identified by the upper TAG_WIDTH
// bits:
//   MARKER_TAG : alignment marker, the low bits carry the marker epoch
//   IDLE_TAG   : idle word, consumed but never forwarded
//   anything else : payload word
//
// Hunt: every lane discards words until its first marker.  From that marker on
// the lane buffers the following words — idles included, because the aligned
// read consumes markers and idles in lockstep and every lane's buffer must hold
// the same word sequence from the alignment point on.  When all lanes have
// reported a marker with the same epoch, the read pointers are set to the
// buffer base and the lanes are released together.  Early lanes have been
// buffering while they waited for the late lanes, so the skew becomes elastic
// buffer fill; DEPTH must cover 2*MARKER_PERIOD + MAX_SKEW so a lane that is a
// whole marker epoch ahead or behind can never overwrite unread data.
//
// Aligned: the head word of every lane is inspected together.  An all-marker
// beat is consumed (never forwarded) after checking that the epochs agree, that
// the epoch followed the previous one and that exactly MARKER_PERIOD payload
// beats have passed; an all-idle beat is consumed silently.  Any mixture (one
// lane is a word ahead or behind) or a spacing/epoch violation drops `aligned`,
// pulses marker_error, bumps realign_count and restarts the hunt.  Payload
// words between the failed marker and the re-lock are lost; this is the usual
// ordered-set re-sync penalty.
//
// The output stream is payload words only.  `marker_ok` pulses on every
// verified in-band marker, which downstream logic can use as a frame/period
// boundary.  One pulse per marker beat, i.e. every MARKER_PERIOD output words
// while the link is clean.
//
// `in_sync` is the per-lane word-framing lock (e.g. the RX comma detector).
// A lane that has not locked its own word boundary never arms.
// -----------------------------------------------------------------------------
module lane_deskew #(
    parameter int LANES         = 4,
    parameter int DATA_WIDTH    = 16,
    parameter int DEPTH         = 64,
    parameter int MAX_SKEW      = 8,
    parameter int MARKER_PERIOD = 16,
    parameter int TAG_WIDTH     = 4,
    parameter logic [TAG_WIDTH-1:0] MARKER_TAG = 4'hA,
    parameter logic [TAG_WIDTH-1:0] IDLE_TAG   = 4'hF
) (
    input  logic                              clk,
    input  logic                              rst_n,
    input  logic                              enable,
    // Per-lane word streams (already in the core clock domain).
    input  logic [LANES-1:0][DATA_WIDTH-1:0]  in_data,
    input  logic [LANES-1:0]                  in_valid,
    input  logic [LANES-1:0]                  in_sync,
    output logic [LANES-1:0]                  in_ready,
    // Deskewed payload stream (markers and idles removed).
    output logic [LANES-1:0][DATA_WIDTH-1:0]  out_data,
    output logic                              out_valid,
    input  logic                              out_ready,
    output logic                              aligned,
    output logic                              marker_ok,
    output logic                              marker_error,
    output logic [9:0]                        realign_count,
    output logic [LANES-1:0]                  lane_locked
);
    localparam int CH_W    = (DEPTH <= 1) ? 1 : $clog2(DEPTH);
    localparam int CNT_W   = CH_W + 1;
    localparam int EPOCH_W = DATA_WIDTH - TAG_WIDTH;

    // Per-lane buffers and pointers.  Each generated lane owns its own
    // registers, so no procedural bit-select of a shared vector is needed.
    logic [DATA_WIDTH-1:0] mem_q [LANES][DEPTH];

    // Per-lane flags exported by the generated lanes for the lane-spanning
    // decisions.
    logic [LANES-1:0]                 armed_x;
    logic [LANES-1:0]                 have_x;
    logic [LANES-1:0]                 head_marker_x;
    logic [LANES-1:0]                 head_idle_x;
    logic [LANES-1:0]                 head_data_x;
    logic [LANES-1:0]                 marker_hit_x;
    logic [LANES-1:0]                 wr_en_x;
    logic [LANES-1:0]                 rd_en_x;
    logic [LANES-1:0][EPOCH_W-1:0]    epoch_x;
    logic [LANES-1:0][EPOCH_W-1:0]    head_epoch_x;
    logic [LANES-1:0][DATA_WIDTH-1:0] head_x;

    logic                        aligned_q;
    logic [EPOCH_W-1:0]          last_epoch_q;
    logic [CNT_W-1:0]            beat_cnt_q;
    logic [9:0]                  realign_q;
    logic                        marker_ok_q;
    logic                        marker_error_q;

    logic have_all, all_marker, all_idle, all_data, mixed;
    logic epochs_equal, align_now, do_realign;
    logic marker_epochs_same, epoch_ok, spacing_ok, marker_check_ok;
    logic [EPOCH_W-1:0] epoch0, head_epoch0;

    assign have_all   = &have_x;
    assign all_marker = (&have_x) & (&head_marker_x);
    assign all_idle   = (&have_x) & (&head_idle_x);
    assign all_data   = (&have_x) & (&head_data_x);
    assign mixed      = have_all & ~all_marker & ~all_idle & ~all_data;

    // -------------------------------------------------------------------------
    // Parameter contract.  These are elaborated, not asserted: a configuration
    // that cannot work must fail closed at build time rather than lose lock at
    // run time.
    // -------------------------------------------------------------------------
    generate
        if (DATA_WIDTH <= TAG_WIDTH) begin : g_no_tag_room
            lane_deskew_needs_data_width_greater_than_tag_width u_reject_tag();
        end
        if (MARKER_PERIOD <= MAX_SKEW) begin : g_period_too_short
            lane_deskew_needs_marker_period_greater_than_max_skew u_reject_period();
        end
        if (DEPTH < 2*MARKER_PERIOD + MAX_SKEW) begin : g_depth_too_small
            lane_deskew_needs_depth_covering_two_marker_periods_plus_skew u_reject_depth();
        end
        if ((DEPTH & (DEPTH-1)) != 0) begin : g_depth_not_pow2
            lane_deskew_needs_power_of_two_depth u_reject_pow2();
        end
    endgenerate

    always_comb begin
        epochs_equal = (&armed_x) & (&in_sync);
        for (int l = 1; l < LANES; l++)
            if (epoch_x[l] != epoch_x[0])
                epochs_equal = 1'b0;
    end

    // Do not start on the same cycle that a lane latches its next marker: that
    // lane would reset its buffer while the others start on the old epoch.
    assign align_now = enable & ~aligned_q & epochs_equal & ~(|marker_hit_x);

    always_comb begin
        marker_epochs_same = 1'b1;
        for (int l = 1; l < LANES; l++)
            if (head_epoch_x[l] != head_epoch_x[0])
                marker_epochs_same = 1'b0;
    end

    assign epoch0       = epoch_x[0];
    assign head_epoch0  = head_epoch_x[0];
    assign epoch_ok     = (head_epoch0 == (last_epoch_q + 1'b1));
    assign spacing_ok   = (beat_cnt_q == CNT_W'(MARKER_PERIOD));
    assign marker_check_ok = marker_epochs_same & epoch_ok & spacing_ok;

    assign do_realign = aligned_q & (mixed | (all_marker & ~marker_check_ok));

    assign out_valid = enable & aligned_q & all_data;

    always_comb begin
        for (int l = 0; l < LANES; l++)
            out_data[l] = out_valid ? head_x[l] : '0;
    end

    // -------------------------------------------------------------------------
    // One generated lane: elastic buffer, pointer bookkeeping and classification.
    // -------------------------------------------------------------------------
    generate
        for (genvar l = 0; l < LANES; l++) begin : g_lane
            logic [CH_W-1:0]  wr_ptr_q;
            logic [CH_W-1:0]  rd_ptr_q;
            logic [CNT_W-1:0] used_q;
            logic             armed_q;
            logic [EPOCH_W-1:0] epoch_q;

            wire [DATA_WIDTH-1:0] w_in   = in_data[l];
            wire [DATA_WIDTH-1:0] w_head = mem_q[l][rd_ptr_q];
            wire is_marker = (w_in[DATA_WIDTH-1 -: TAG_WIDTH] == MARKER_TAG);
            wire head_marker = (w_head[DATA_WIDTH-1 -: TAG_WIDTH] == MARKER_TAG);
            wire head_idle   = (w_head[DATA_WIDTH-1 -: TAG_WIDTH] == IDLE_TAG);
            wire head_data   = ~head_marker & ~head_idle;
            wire [CNT_W-1:0] used = used_q;
            wire full = (used >= CNT_W'(DEPTH));

            assign in_ready[l] = enable &
                                 ((~aligned_q & ~armed_q) | ~full);
            // Buffering starts at a lane's marker.  Idles are kept so every
            // lane's buffer has the same word sequence from the alignment
            // point on; the aligned read consumes them in lockstep.
            assign wr_en_x[l]  = in_valid[l] & in_ready[l] &
                                 (aligned_q | (armed_q & ~is_marker));
            assign rd_en_x[l]  = aligned_q & have_all &
                                 (all_marker | all_idle | (all_data & out_ready));

            assign armed_x[l]       = armed_q;
            assign have_x[l]        = (used_q != '0);
            assign head_marker_x[l] = head_marker;
            assign head_idle_x[l]   = head_idle;
            assign head_data_x[l]   = head_data;
            assign marker_hit_x[l]  = in_valid[l] & in_ready[l] & in_sync[l] &
                                      is_marker;
            assign epoch_x[l]       = epoch_q;
            assign head_epoch_x[l]  = w_head[EPOCH_W-1:0];
            assign head_x[l]        = w_head;
            assign lane_locked[l]   = in_sync[l] & (aligned_q | armed_q);

            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    wr_ptr_q <= '0;
                    rd_ptr_q <= '0;
                    used_q   <= '0;
                    armed_q  <= 1'b0;
                    epoch_q  <= '0;
                end else if (!enable) begin
                    wr_ptr_q <= '0;
                    rd_ptr_q <= '0;
                    used_q   <= '0;
                    armed_q  <= 1'b0;
                end else if (!aligned_q) begin
                    if (align_now)
                        rd_ptr_q <= '0;
                    if (marker_hit_x[l]) begin
                        wr_ptr_q <= '0;
                        used_q   <= '0;
                        armed_q  <= 1'b1;
                        epoch_q  <= w_in[EPOCH_W-1:0];
                    end else if (wr_en_x[l]) begin
                        mem_q[l][wr_ptr_q] <= w_in;
                        wr_ptr_q <= wr_ptr_q + 1'b1;
                        used_q   <= used_q + 1'b1;
                    end
                end else if (do_realign) begin
                    armed_q <= 1'b0;
                end else begin
                    if (wr_en_x[l]) begin
                        mem_q[l][wr_ptr_q] <= w_in;
                        wr_ptr_q <= wr_ptr_q + 1'b1;
                    end
                    if (rd_en_x[l])
                        rd_ptr_q <= rd_ptr_q + 1'b1;
                    if (wr_en_x[l] & rd_en_x[l])
                        used_q <= used_q;
                    else if (wr_en_x[l])
                        used_q <= used_q + 1'b1;
                    else if (rd_en_x[l])
                        used_q <= used_q - 1'b1;
                end
            end
        end
    endgenerate

    // The aligned/beat bookkeeping is lane-spanning.
    assign aligned       = aligned_q;
    assign marker_ok     = marker_ok_q;
    assign marker_error  = marker_error_q;
    assign realign_count = realign_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            aligned_q      <= 1'b0;
            last_epoch_q   <= '0;
            beat_cnt_q     <= '0;
            realign_q      <= '0;
            marker_ok_q    <= 1'b0;
            marker_error_q <= 1'b0;
        end else if (!enable) begin
            aligned_q      <= 1'b0;
            beat_cnt_q     <= '0;
            marker_ok_q    <= 1'b0;
            marker_error_q <= 1'b0;
        end else begin
            marker_ok_q    <= 1'b0;
            marker_error_q <= 1'b0;

            if (!aligned_q) begin
                if (align_now) begin
                    aligned_q    <= 1'b1;
                    beat_cnt_q   <= '0;
                    last_epoch_q <= epoch0;
                end
            end else if (do_realign) begin
                aligned_q      <= 1'b0;
                beat_cnt_q     <= '0;
                marker_error_q <= 1'b1;
                realign_q      <= realign_q + 1'b1;
            end else if (all_marker) begin
                marker_ok_q  <= 1'b1;
                beat_cnt_q   <= '0;
                last_epoch_q <= head_epoch0;
            end else if (all_data) begin
                if (out_ready)
                    beat_cnt_q <= beat_cnt_q + 1'b1;
            end
        end
    end

endmodule
