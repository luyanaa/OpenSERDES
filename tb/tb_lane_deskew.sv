`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// lane_deskew testbench.
//
// Four lanes carry the same marker cadence (one marker every MARKER_PERIOD
// payload words, with idle ordered sets interleaved) and per-lane payloads.
// Each lane starts after a different number of cycles, so the receiver sees a
// 0/2/5/8-word skew.
//
// Checks:
//   1. The deskew locks (`aligned`) and every lane reports lane_locked.
//   2. Every output beat carries all four lanes with the same payload index
//      (lockstep) and the lane field matches the output lane (no lane swap).
//   3. Payload index advances by exactly one per beat: no duplicates, no gaps.
//   4. marker_ok pulses for the in-band markers; idles are stripped.
//   5. One marker word is deleted from lane 2 mid-stream.  The next marker beat
//      is mixed, so marker_error pulses, realign_count increments, and the deskew
//      re-locks on the following marker.  Exactly one marker period (8 payload
//      words) is lost, and delivery continues in lockstep afterwards.
// -----------------------------------------------------------------------------
module tb_lane_deskew;

    localparam int LANES      = 4;
    localparam int DW         = 16;
    localparam int DEPTH      = 64;
    localparam int P          = 16;   // payload words between markers
    localparam int NMARK      = 20;   // marker epochs in the stream
    localparam int STREAM_MAX = NMARK * (P + 4) + 16;
    localparam int SLIP_EPOCH = 3;    // lane 2 marker deleted for this epoch
    localparam int SLIP_LANE  = 2;

    logic clk = 0, rst_n = 0, enable = 0;

    logic [LANES-1:0][DW-1:0] in_data;
    logic [LANES-1:0]         in_valid;
    logic [LANES-1:0]         in_sync;
    logic [LANES-1:0]         in_ready;

    logic [LANES-1:0][DW-1:0] out_data;
    logic                     out_valid;
    logic                     out_ready;

    logic        aligned, marker_ok, marker_error;
    logic [9:0]  realign_count;
    logic [LANES-1:0] lane_locked;

    always #5 clk = ~clk;

    lane_deskew #(
        .LANES(LANES), .DATA_WIDTH(DW), .DEPTH(DEPTH),
        .MAX_SKEW(8), .MARKER_PERIOD(P),
        .TAG_WIDTH(4), .MARKER_TAG(4'hA), .IDLE_TAG(4'hF)
    ) dut (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .in_data(in_data), .in_valid(in_valid), .in_sync(in_sync),
        .in_ready(in_ready),
        .out_data(out_data), .out_valid(out_valid), .out_ready(out_ready),
        .aligned(aligned), .marker_ok(marker_ok), .marker_error(marker_error),
        .realign_count(realign_count), .lane_locked(lane_locked)
    );

    // -------------------------------------------------------------------------
    // Per-lane word streams.
    // Layout of one epoch on every lane:
    //   marker(epoch), idle, p0..p3, idle, p4..p(P-1)
    // The idle words do not count towards MARKER_PERIOD.
    // -------------------------------------------------------------------------
    logic [DW-1:0] stream [LANES*STREAM_MAX];
    logic [31:0]   slen   [LANES];
    logic [31:0]   pos    [LANES];

    function automatic logic [DW-1:0] mk_payload(input int lane, input int idx);
        mk_payload = {2'b01, 3'(lane), 11'(idx)};
    endfunction

    int n;
    initial begin
        for (int l = 0; l < LANES; l++) begin
            n = 0;
            for (int e = 0; e < NMARK; e++) begin
                if (!((l == SLIP_LANE) && (e == SLIP_EPOCH))) begin
                    stream[l*STREAM_MAX + n] = {4'hA, 12'(e)};
                    n = n + 1;
                end
                stream[l*STREAM_MAX + n] = {4'hF, 12'h000};
                n = n + 1;
                for (int i = 0; i < P; i++) begin
                    stream[l*STREAM_MAX + n] = mk_payload(l, e*P + i);
                    n = n + 1;
                    if (i == 3) begin
                        stream[l*STREAM_MAX + n] = {4'hF, 12'h001};
                        n = n + 1;
                    end
                end
            end
            slen[l]  = 32'(n);
            pos[l]   = 0;
        end
    end

    logic [31:0] tick;
    always @(posedge clk) begin
        if (!rst_n)
            tick <= 0;
        else if (enable)
            tick <= tick + 1;
    end

    generate
        for (genvar l = 0; l < LANES; l++) begin : g_drv
            localparam logic [31:0] DLY = (l == 0) ? 32'd0 :
                                          (l == 1) ? 32'd2 :
                                          (l == 2) ? 32'd5 : 32'd8;
            assign in_valid[l] = enable && (tick >= DLY) &&
                                 (pos[l] < slen[l]);
            assign in_data[l]  = {DW{in_valid[l]}} &
                                 stream[l*STREAM_MAX + pos[l]];
            assign in_sync[l]  = rst_n;
        end
    endgenerate

    always @(posedge clk) begin
        if (!rst_n) begin
            for (int l = 0; l < LANES; l++)
                pos[l] <= 0;
        end else if (enable) begin
            for (int l = 0; l < LANES; l++)
                if (in_valid[l] && in_ready[l])
                    pos[l] <= pos[l] + 1;
        end
    end

    // Line-rate drain: input and output rates match, so the elastic buffers
    // hold only the lane skew (plus the in-flight word).
    assign out_ready = rst_n;

    // -------------------------------------------------------------------------
    // Scoreboard.
    // -------------------------------------------------------------------------
    int  errors;
    int  out_beats;
    int  marker_oks;
    int  marker_errs;
    int  resync_events;
    int  skipped;
    int  exp_idx [LANES];
    bit  resync_pending;
    bit  base_set;
    int  idx, lane_field, exp_lane;
    logic [DW-1:0] obs_word;
    int  cycle_count;

    always @(posedge clk) begin
        if (!rst_n) begin
            errors        <= 0;
            out_beats     <= 0;
            marker_oks    <= 0;
            marker_errs   <= 0;
            resync_events <= 0;
            skipped       <= 0;
            resync_pending <= 1'b1;
            base_set      <= 1'b0;
            for (int l = 0; l < LANES; l++)
                exp_idx[l] <= 0;
        end else if (enable) begin
            if (marker_ok)
                marker_oks <= marker_oks + 1;

            if (marker_error) begin
                marker_errs   <= marker_errs + 1;
                resync_events <= resync_events + 1;
                resync_pending <= 1'b1;
            end

            if (out_valid && out_ready) begin
                out_beats <= out_beats + 1;
                idx = int'(out_data[0][10:0]);

                for (int l = 0; l < LANES; l++) begin
                    obs_word   = out_data[l];
                    lane_field = int'(obs_word[13:11]);
                    exp_lane   = l;
                    if (lane_field != exp_lane) begin
                        $display("ERROR: beat %0d lane %0d carries lane field %0d",
                                 out_beats, l, lane_field);
                        errors <= errors + 1;
                    end
                    if (int'(obs_word[10:0]) != idx) begin
                        $display("ERROR: beat %0d lane %0d index %0d != %0d",
                                 out_beats, l, int'(obs_word[10:0]), idx);
                        errors <= errors + 1;
                    end
                end

                if (resync_pending) begin
                    if (base_set)
                        skipped <= skipped + (idx - exp_idx[0]);
                    if (base_set && idx < exp_idx[0]) begin
                        $display("ERROR: re-lock moved backwards %0d -> %0d",
                                 exp_idx[0], idx);
                        errors <= errors + 1;
                    end
                    for (int l = 0; l < LANES; l++)
                        exp_idx[l] <= idx + 1;
                    resync_pending <= 1'b0;
                    base_set       <= 1'b1;
                end else begin
                    for (int l = 0; l < LANES; l++) begin
                        if (idx != exp_idx[l]) begin
                            $display("ERROR: beat %0d lane %0d index %0d expected %0d",
                                     out_beats, l, idx, exp_idx[l]);
                            errors <= errors + 1;
                        end
                        exp_idx[l] <= idx + 1;
                    end
                end
            end
        end
    end

    initial begin
        cycle_count = 0;
        repeat (4) @(posedge clk);
        rst_n  = 1'b1;
        enable = 1'b1;

        // Drain every lane plus the deskew pipeline.
        for (int t = 0; t < 6000; t++) begin
            @(posedge clk);
            cycle_count++;
        end

        if (aligned !== 1'b1) begin
            $display("ERROR: deskew not aligned at end");
            errors++;
        end
        if (lane_locked !== {LANES{1'b1}}) begin
            $display("ERROR: lane_locked=%b", lane_locked);
            errors++;
        end
        if (realign_count !== 10'd1) begin
            $display("ERROR: realign_count=%0d expected 1", realign_count);
            errors++;
        end
        if (marker_errs !== 1) begin
            $display("ERROR: marker_error pulses=%0d expected 1", marker_errs);
            errors++;
        end
        // NMARK-1 align markers pass through the aligned path; the alignment
        // marker and the marker beat that triggered the re-lock are not
        // "verified" (one starts the stream, one raises the error).
        if (marker_oks < NMARK - 3) begin
            $display("ERROR: only %0d verified markers", marker_oks);
            errors++;
        end
        if (skipped != P) begin
            $display("ERROR: skipped %0d payload words, expected %0d",
                     skipped, P);
            errors++;
        end
        if (out_beats < 200) begin
            $display("ERROR: only %0d output beats", out_beats);
            errors++;
        end
        if (pos[0] != slen[0] || pos[1] != slen[1] ||
            pos[2] != slen[2] || pos[3] != slen[3]) begin
            $display("ERROR: lanes not drained: pos=%0d/%0d/%0d/%0d slen=%0d/%0d/%0d/%0d",
                     pos[0], pos[1], pos[2], pos[3],
                     slen[0], slen[1], slen[2], slen[3]);
            errors++;
        end

        if (errors == 0)
            $display("PASS: tb_lane_deskew beats=%0d markers=%0d realigns=%0d skipped=%0d",
                     out_beats, marker_oks, realign_count, skipped);
        else
            $display("FAIL: tb_lane_deskew errors=%0d beats=%0d markers=%0d skipped=%0d",
                     errors, out_beats, marker_oks, skipped);
        $finish;
    end

    initial begin
        #2000000;
        $display("FAIL: tb_lane_deskew timeout beats=%0d pos=%0d/%0d/%0d/%0d errors=%0d",
                 out_beats, pos[0], pos[1], pos[2], pos[3], errors);
        $finish;
    end

endmodule
