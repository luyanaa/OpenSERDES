`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// axis_reliable_tx + axis_reliable_rx over a corruptible AXIS wire.
//
// Sequence of events:
//   1. Frames 0..5 are queued.  All are delivered in order.
//   2. The first ACK(2) is dropped and the ACK path is muted until the
//      transmitter's timeout fires.  The go-back retransmission is detected as
//      duplicates by the receiver, dropped, and re-acknowledged cumulatively.
//      Nothing is delivered twice.
//   3. Frames 6..8 are queued; the payload of the first transmission of frame 7
//      is corrupted, so its CRC fails.  The receiver NAKs the missing sequence
//      and the transmitter retransmits it (go-back-N).
// Checks: byte-exact in-order delivery of all 9 frames exactly once, at least
// one timeout retry, at least one NAK, at least one duplicate re-ACK, no link
// error, and no frame errors.
// -----------------------------------------------------------------------------
module tb_axis_reliable_link;

    localparam int DW       = 8;
    localparam int SEQ_W    = 4;
    localparam int WIN      = 4;
    localparam int MAXB     = 16;
    localparam int TIMEOUT  = 40;
    localparam int NLEN     = 9;
    localparam int CORRUPT_BEAT = 2;

    logic clk, rst_n, enable, clear;

    logic p_valid, p_ready, p_last;
    logic [DW-1:0] p_data;

    logic w_valid, w_ready, w_last, w_tuser;
    logic [DW-1:0] w_data;

    logic r_valid, r_ready, r_last, r_tuser;
    logic [DW-1:0] r_data;

    logic [7:0] tx_retry;
    logic tx_link_error, tx_busy;

    logic ack_valid, nak_valid;
    logic [SEQ_W-1:0] ack_seq, nak_seq;
    logic crc_error, frame_drop;
    logic [15:0] frames_ok, frames_dup;

    logic o_valid, o_ready, o_last;
    logic [DW-1:0] o_data;

    int errors;
    int sent;
    int delivered;
    int oframes;
    int exp_idx;
    int exp_byte;
    int max_retry;
    int nak_count;
    int rnd;
    logic ack_gate;
    logic ack2_seen;
    logic ack2_block;

    int wire_beat;
    logic corrupt_arm, corrupt_hit, seen7;
    int corrupt_beat;
    logic [3:0] wire_hdr;

    int lens [0:NLEN-1];
    logic [DW-1:0] exp_data [0:4095];

    always #5 clk = ~clk;

    assign ack2_block  = ack_valid && (ack_seq == SEQ_W'(2)) && !ack2_seen;

    axis_reliable_tx #(
        .DATA_WIDTH(DW), .SEQ_W(SEQ_W), .WINDOW(WIN),
        .MAX_FRAME_BEATS(MAXB), .TIMEOUT_CYCLES(TIMEOUT),
        .RETRY_LIMIT(8)
    ) u_tx (
        .clk(clk), .rst_n(rst_n), .enable(enable), .clear(clear),
        .s_axis_tvalid(p_valid), .s_axis_tready(p_ready),
        .s_axis_tdata(p_data), .s_axis_tlast(p_last),
        .m_axis_tvalid(w_valid), .m_axis_tready(w_ready),
        .m_axis_tdata(w_data), .m_axis_tlast(w_last),
        .m_axis_tuser(w_tuser),
        .ack_valid(ack_valid && ack_gate && !ack2_block),
        .ack_seq(ack_seq),
        .nak_valid(nak_valid), .nak_seq(nak_seq),
        .retry_count(tx_retry), .link_error(tx_link_error),
        .busy(tx_busy)
    );

    axis_reliable_rx #(
        .DATA_WIDTH(DW), .SEQ_W(SEQ_W), .WINDOW(WIN), .MAX_FRAME_BEATS(MAXB)
    ) u_rx (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .s_axis_tvalid(r_valid), .s_axis_tready(r_ready),
        .s_axis_tdata(r_data), .s_axis_tlast(r_last),
        .s_axis_tuser(r_tuser),
        .m_axis_tvalid(o_valid), .m_axis_tready(o_ready),
        .m_axis_tdata(o_data), .m_axis_tlast(o_last),
        .ack_valid(ack_valid), .ack_seq(ack_seq),
        .nak_valid(nak_valid), .nak_seq(nak_seq),
        .crc_error(crc_error), .frame_drop(frame_drop),
        .frames_ok(frames_ok), .frames_dup(frames_dup)
    );

    // Corruptible wire: handshake and sidebands pass through, only the
    // selected payload byte is flipped.
    assign r_valid = w_valid;
    assign w_ready = r_ready;
    assign r_last  = w_last;
    assign r_tuser = w_tuser;
    assign r_data  = corrupt_hit ? (w_data ^ 8'h40) : w_data;

    // ~6% consumer backpressure.
    always @(posedge clk) rnd <= {rnd[30:0], rnd[30] ^ rnd[27]};
    assign o_ready = rnd[7];

    // First-transmission detection for sequence 7.
    always @(posedge clk) begin
        if (!rst_n) begin
            wire_beat    <= 0;
            corrupt_arm  <= 1'b0;
            seen7        <= 1'b0;
            corrupt_beat <= CORRUPT_BEAT;
        end else if (w_valid && w_ready) begin
            if (wire_beat == 0) begin
                wire_hdr = w_data[SEQ_W-1:0];
                if (wire_hdr == SEQ_W'(7) && !seen7) begin
                    seen7       <= 1'b1;
                    corrupt_arm <= 1'b1;
                end
            end
            if (w_last) begin
                wire_beat   <= 0;
                corrupt_arm <= 1'b0;
            end else begin
                wire_beat <= wire_beat + 1;
            end
        end
    end

    assign corrupt_hit = corrupt_arm && (wire_beat == corrupt_beat);

    // ACK gating: drop ACK(2) and everything after it until the transmitter
    // has actually timed out and retried.
    always @(posedge clk) begin
        if (!rst_n) begin
            ack_gate  <= 1'b1;
            ack2_seen <= 1'b0;
        end else begin
            if (ack2_block) begin
                ack2_seen <= 1'b1;
                ack_gate  <= 1'b0;
            end else if (!ack_gate && (tx_retry != 0)) begin
                ack_gate <= 1'b1;
            end
            if (tx_retry > max_retry)
                max_retry <= int'(tx_retry);
            if (nak_valid)
                nak_count <= nak_count + 1;
        end
    end

    // Output scoreboard: exact frame order, byte values and tlast placement.
    always @(posedge clk) begin
        if (rst_n && o_valid && o_ready) begin
            if (delivered >= 4096) begin
                $display("ERROR: output overrun");
                errors++;
            end else begin
                if (o_data !== exp_data[delivered]) begin
                    $display("ERROR: byte %0d got %02h expected %02h",
                             delivered, o_data, exp_data[delivered]);
                    errors++;
                end
                if (o_last !== (exp_byte == lens[exp_idx]-1)) begin
                    $display("ERROR: tlast wrong at frame %0d byte %0d",
                             exp_idx, exp_byte);
                    errors++;
                end
                delivered++;
                if (o_last) begin
                    oframes++;
                    exp_idx++;
                    exp_byte = 0;
                end else begin
                    exp_byte++;
                end
            end
        end
    end

    task automatic send_frame(input logic [DW-1:0] base, input int len);
        begin
            for (int k = 0; k < len; k++) begin
                @(negedge clk);
                p_valid = 1'b1;
                p_data  = base + DW'(k);
                p_last  = (k == len-1);
                while (!p_ready) @(negedge clk);
                @(posedge clk);
                #1;
                p_valid = 1'b0;
                p_last  = 1'b0;
            end
            sent++;
        end
    endtask

    initial begin
        lens[0] = 4; lens[1] = 1; lens[2] = 7;
        lens[3] = 2; lens[4] = 5; lens[5] = 3;
        lens[6] = 2; lens[7] = 4; lens[8] = 6;

        clk = 1'b0; rst_n = 1'b0; enable = 1'b0; clear = 1'b0;
        p_valid = 1'b0; p_data = '0; p_last = 1'b0;
        rnd = 32'h1234_5678;
        errors = 0; sent = 0; delivered = 0; oframes = 0;
        exp_idx = 0; exp_byte = 0; max_retry = 0; nak_count = 0;

        exp_idx = 0;
        for (int f = 0; f < NLEN; f++)
            for (int k = 0; k < lens[f]; k++)
                exp_data[exp_idx++] = DW'((f*16 + k) & 8'hFF);
        exp_idx = 0;

        repeat (4) @(posedge clk);
        rst_n  = 1'b1;
        enable = 1'b1;
        repeat (4) @(posedge clk);

        // ---- Phase 1: six clean frames, but ACK(2) is swallowed ----------
        for (int f = 0; f < 6; f++)
            send_frame(DW'(f*16), lens[f]);

        // All six must be delivered even though the transmitter had to time
        // out and go back.
        for (int t = 0; t < 200000 && frames_ok < 6; t++)
            @(posedge clk);
        if (frames_ok != 6) begin
            $display("ERROR: phase 1 delivered %0d frames", frames_ok);
            errors++;
        end
        // The dropped ACK must now force a timeout go-back.
        for (int t = 0; t < 20000 && max_retry < 1; t++)
            @(posedge clk);
        if (max_retry < 1) begin
            $display("ERROR: timeout go-back never happened (retry=%0d)", max_retry);
            errors++;
        end

        // ---- Phase 2: corrupt the first transmission of frame 7 -----------
        for (int f = 6; f < NLEN; f++)
            send_frame(DW'(f*16), lens[f]);

        for (int t = 0; t < 200000 && frames_ok < NLEN; t++)
            @(posedge clk);
        repeat (50) @(posedge clk);

        if (frames_ok != NLEN) begin
            $display("ERROR: delivered %0d of %0d frames", frames_ok, NLEN);
            errors++;
        end
        if (oframes != NLEN) begin
            $display("ERROR: output frames %0d expected %0d", oframes, NLEN);
            errors++;
        end
        if (nak_count < 1) begin
            $display("ERROR: no NAK observed for the corrupted frame");
            errors++;
        end
        if (frames_dup < 1) begin
            $display("ERROR: no duplicate went through the duplicate path");
            errors++;
        end
        if (tx_link_error) begin
            $display("ERROR: link_error asserted");
            errors++;
        end
        if (frame_drop !== 1'b0) begin
            $display("ERROR: frame_drop asserted");
            errors++;
        end

        if (errors == 0)
            $display("PASS: tb_axis_reliable_link frames=%0d bytes=%0d retries=%0d naks=%0d dups=%0d",
                     oframes, delivered, max_retry, nak_count, frames_dup);
        else
            $display("FAIL: tb_axis_reliable_link errors=%0d", errors);
        $finish;
    end

    initial begin
        #4000000;
        $display("FAIL: tb_axis_reliable_link timeout frames=%0d errors=%0d",
                 frames_ok, errors);
        $finish;
    end

endmodule
