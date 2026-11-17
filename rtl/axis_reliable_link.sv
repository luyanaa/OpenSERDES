`timescale 1ns/1ps

/* verilator lint_off DECLFILENAME */

// -----------------------------------------------------------------------------
// Minimal reliable transport over AXI4-Stream.
//
// Wire frame (one AXIS frame):
//   beat 0     : header, {padding, seq[SEQ_W-1:0]}
//   beat 1..N  : payload (N >= 1)
//   beat N+1.. : CRC beats produced by axis_crc_inserter, tlast on the last one
//   tuser      : marks the last *payload* beat (consumed by the CRC checker)
//
// The header travels inside the CRC-protected body, so a corrupted sequence
// number is rejected like any other payload corruption.
//
// axis_reliable_tx
//   - Buffers up to WINDOW frames of MAX_FRAME_BEATS beats (WINDOW must be a
//     power of two: the slot index is the low bits of the sequence number).
//   - Sends every unacknowledged frame in order (go-back-N).
//   - ack_seq is cumulative: ACK(n) retires every frame up to and including n.
//   - nak_seq restarts the send pointer at n.
//   - A frame that stays unacknowledged for TIMEOUT_CYCLES while the sender is
//     idle restarts from ack_base (timeout go-back).
//   - retry_count counts NAK/timeout restarts and clears on ACK progress.
//     link_error is sticky after RETRY_LIMIT restarts (cleared by reset/clear).
//
// axis_reliable_rx
//   - CRC-checks with axis_crc_checker STORE_AND_FORWARD=1: a bad frame is
//     dropped before anything reaches m_axis.
//   - Delivers only the in-order frame (seq == expected); duplicates are
//     dropped and re-acknowledged with a cumulative ACK, future frames are
//     dropped and NAK'd with the expected sequence number.
//
// The pair is protocol logic, not a PCS: tuser/tlast are sideband signals that
// a serial link would have to carry or encode.
// -----------------------------------------------------------------------------

module axis_reliable_tx #(
    parameter int DATA_WIDTH      = 8,
    parameter int SEQ_W           = 4,
    parameter int WINDOW          = 4,
    parameter int MAX_FRAME_BEATS = 64,
    parameter int TIMEOUT_CYCLES  = 256,
    parameter int RETRY_LIMIT     = 8,
    parameter int CRC_WIDTH       = 16,
    parameter logic [CRC_WIDTH-1:0] CRC_POLY = 16'h1021,
    parameter logic [CRC_WIDTH-1:0] CRC_INIT = '1
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  enable,
    input  logic                  clear,
    // Local payload ingress.
    input  logic                  s_axis_tvalid,
    output logic                  s_axis_tready,
    input  logic [DATA_WIDTH-1:0] s_axis_tdata,
    input  logic                  s_axis_tlast,
    // Wire egress (header + payload + CRC).
    output logic                  m_axis_tvalid,
    input  logic                  m_axis_tready,
    output logic [DATA_WIDTH-1:0] m_axis_tdata,
    output logic                  m_axis_tlast,
    output logic                  m_axis_tuser,
    // Acknowledgement sideband from the peer receiver.
    input  logic                  ack_valid,
    input  logic [SEQ_W-1:0]      ack_seq,
    input  logic                  nak_valid,
    input  logic [SEQ_W-1:0]      nak_seq,
    output logic [7:0]            retry_count,
    output logic                  link_error,
    output logic                  busy
);
    localparam int IDX_W = (WINDOW <= 1) ? 1 : $clog2(WINDOW);
    localparam int AW    = (MAX_FRAME_BEATS <= 1) ? 1 : $clog2(MAX_FRAME_BEATS);
    localparam int TW    = (TIMEOUT_CYCLES <= 1) ? 1 : $clog2(TIMEOUT_CYCLES + 1);
    localparam int DEPTH = WINDOW * MAX_FRAME_BEATS;

    typedef enum logic [1:0] { S_IDLE, S_HDR, S_PAY, S_TAIL } state_t;
    state_t state_q;

    logic [DATA_WIDTH-1:0] buf_q [DEPTH];
    logic [AW:0]           len_q [WINDOW];

    logic [SEQ_W-1:0] ack_base_q, next_seq_q, send_seq_q, hdr_seq_q;
    logic [AW:0]      wr_ptr_q, rd_ptr_q;
    logic [IDX_W-1:0] rd_slot_q;
    logic [TW-1:0]    timeout_q;
    logic [7:0]       retry_count_q;
    logic             link_error_q;

    // CRC inserter feed (header + payload, tlast on the last payload beat).
    logic                  crc_s_valid, crc_s_last;
    logic [DATA_WIDTH-1:0] crc_s_data;
    logic                  crc_s_ready;

    // Window arithmetic in SEQ_W+1 bits: used_now is the number of stored but
    // not cumulatively acknowledged frames.
    wire [SEQ_W:0] used_now = {1'b0, next_seq_q} - {1'b0, ack_base_q};
    wire [SEQ_W:0] ack_off  = {1'b0, ack_seq}    - {1'b0, ack_base_q};
    wire [SEQ_W:0] snd_off  = {1'b0, send_seq_q} - {1'b0, ack_base_q};
    wire [SEQ_W:0] nak_off  = {1'b0, nak_seq}    - {1'b0, ack_base_q};

    wire ack_in_window = ack_valid && (used_now != '0) && (ack_off < used_now);
    wire nak_in_window = nak_valid && (used_now != '0) && (nak_off < used_now);

    wire [DATA_WIDTH-1:0] hdr_data = DATA_WIDTH'(hdr_seq_q);

    logic [DATA_WIDTH-1:0] rd_data;
    logic [AW:0]           rd_len;

    assign rd_data = buf_q[int'(rd_slot_q)*MAX_FRAME_BEATS +
                            int'(rd_ptr_q[AW-1:0])];
    assign rd_len  = len_q[rd_slot_q];

    assign s_axis_tready = enable && !link_error_q && !clear &&
                           (used_now < (SEQ_W+1)'(WINDOW));

    assign crc_s_valid = enable && !clear &&
                         ((state_q == S_HDR) || (state_q == S_PAY));
    assign crc_s_data  = (state_q == S_HDR) ? hdr_data : rd_data;
    assign crc_s_last  = (state_q == S_PAY) && (rd_ptr_q + 1'b1 == rd_len);

    axis_crc_inserter #(
        .DATA_WIDTH(DATA_WIDTH), .CRC_WIDTH(CRC_WIDTH),
        .POLY(CRC_POLY), .INIT(CRC_INIT)
    ) u_crc (
        .clk(clk), .rst_n(rst_n), .enable(enable && !clear),
        .s_axis_tvalid(crc_s_valid), .s_axis_tready(crc_s_ready),
        .s_axis_tdata(crc_s_data), .s_axis_tlast(crc_s_last),
        .m_axis_tvalid(m_axis_tvalid), .m_axis_tready(m_axis_tready),
        .m_axis_tdata(m_axis_tdata), .m_axis_tlast(m_axis_tlast),
        .m_axis_tuser(m_axis_tuser)
        /* verilator lint_off PINCONNECTEMPTY */
        , .crc_word()
        /* verilator lint_on PINCONNECTEMPTY */
    );

    assign retry_count = retry_count_q;
    assign link_error  = link_error_q;
    assign busy        = (state_q != S_IDLE) || (used_now != '0);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_q       <= S_IDLE;
            ack_base_q    <= '0;
            next_seq_q    <= '0;
            send_seq_q    <= '0;
            hdr_seq_q     <= '0;
            wr_ptr_q      <= '0;
            rd_ptr_q      <= '0;
            rd_slot_q     <= '0;
            timeout_q     <= '0;
            retry_count_q <= '0;
            link_error_q  <= 1'b0;
            for (int i = 0; i < WINDOW; i++)
                len_q[i] <= '0;
        end else if (!enable || clear) begin
            state_q       <= S_IDLE;
            ack_base_q    <= '0;
            next_seq_q    <= '0;
            send_seq_q    <= '0;
            wr_ptr_q      <= '0;
            rd_ptr_q      <= '0;
            rd_slot_q     <= '0;
            timeout_q     <= '0;
            retry_count_q <= '0;
            link_error_q  <= 1'b0;
            for (int i = 0; i < WINDOW; i++)
                len_q[i] <= '0;
        end else begin
            // ---------------------------------------------------------------
            // Frame storage.
            // ---------------------------------------------------------------
            if (s_axis_tvalid && s_axis_tready) begin
                buf_q[int'(next_seq_q[IDX_W-1:0])*MAX_FRAME_BEATS +
                      int'(wr_ptr_q[AW-1:0])] <= s_axis_tdata;
                if (s_axis_tlast) begin
                    len_q[next_seq_q[IDX_W-1:0]] <= wr_ptr_q + 1'b1;
                    wr_ptr_q <= '0;
                    next_seq_q <= next_seq_q + 1'b1;
                end else begin
                    wr_ptr_q <= wr_ptr_q + 1'b1;
                end
            end

            // ---------------------------------------------------------------
            // Sender state machine.  send_seq_q is the next frame to start;
            // it advances when a frame starts, so a completion cannot skip a
            // frame that an ACK has already moved past.
            // ---------------------------------------------------------------
            case (state_q)
                S_IDLE: begin
                    if (send_seq_q != next_seq_q) begin
                        hdr_seq_q <= send_seq_q;
                        rd_slot_q <= send_seq_q[IDX_W-1:0];
                        rd_ptr_q  <= '0;
                        send_seq_q <= send_seq_q + 1'b1;
                        state_q    <= S_HDR;
                    end
                end
                S_HDR: begin
                    if (crc_s_ready) begin
                        rd_ptr_q <= '0;
                        state_q  <= S_PAY;
                    end
                end
                S_PAY: begin
                    if (crc_s_ready) begin
                        if (crc_s_last) begin
                            state_q <= S_TAIL;
                        end else begin
                            rd_ptr_q <= rd_ptr_q + 1'b1;
                        end
                    end
                end
                S_TAIL: begin
                    // The CRC beats are emitted after the payload; the frame is
                    // complete on the final one.
                    if (m_axis_tvalid && m_axis_tready && m_axis_tlast) begin
                        timeout_q <= '0;
                        state_q   <= S_IDLE;
                    end
                end
                default: state_q <= S_IDLE;
            endcase

            // ---------------------------------------------------------------
            // ACK / NAK / timeout bookkeeping.  A frame in flight still
            // completes; the pointers decide what happens next.
            // ---------------------------------------------------------------
            if (ack_in_window) begin
                ack_base_q    <= ack_seq + 1'b1;
                retry_count_q <= '0;
                timeout_q     <= '0;
                // A queued go-back retransmission at or below the new
                // ack_base is retired with the ACK.
                if (snd_off <= ack_off)
                    send_seq_q <= ack_seq + 1'b1;
            end

            if (nak_in_window) begin
                send_seq_q    <= nak_seq;
                timeout_q     <= '0;
                if (retry_count_q != 8'hFF)
                    retry_count_q <= retry_count_q + 1'b1;
                if (retry_count_q + 1'b1 >= 8'(RETRY_LIMIT))
                    link_error_q <= 1'b1;
            end else if (!ack_in_window && (state_q == S_IDLE) &&
                         (used_now != '0)) begin
                // Count only while waiting: a frame that is still being
                // transmitted must not trip the timeout.
                if (timeout_q == TW'(TIMEOUT_CYCLES - 1)) begin
                    send_seq_q <= ack_base_q;
                    timeout_q  <= '0;
                    if (retry_count_q != 8'hFF)
                        retry_count_q <= retry_count_q + 1'b1;
                    if (retry_count_q + 1'b1 >= 8'(RETRY_LIMIT))
                        link_error_q <= 1'b1;
                end else begin
                    timeout_q <= timeout_q + 1'b1;
                end
            end
        end
    end

endmodule


module axis_reliable_rx #(
    parameter int DATA_WIDTH      = 8,
    parameter int SEQ_W           = 4,
    // Frames older than expected by at most WINDOW are duplicates; anything
    // else that is not the expected frame is treated as out of order.
    parameter int WINDOW          = 4,
    parameter int MAX_FRAME_BEATS = 64,
    parameter int CRC_WIDTH       = 16,
    parameter logic [CRC_WIDTH-1:0] CRC_POLY = 16'h1021,
    parameter logic [CRC_WIDTH-1:0] CRC_INIT = '1
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  enable,
    // Wire ingress.
    input  logic                  s_axis_tvalid,
    output logic                  s_axis_tready,
    input  logic [DATA_WIDTH-1:0] s_axis_tdata,
    input  logic                  s_axis_tlast,
    input  logic                  s_axis_tuser,
    // Delivered payload.
    output logic                  m_axis_tvalid,
    input  logic                  m_axis_tready,
    output logic [DATA_WIDTH-1:0] m_axis_tdata,
    output logic                  m_axis_tlast,
    // Acknowledgement sideband back to the peer transmitter.
    output logic                  ack_valid,
    output logic [SEQ_W-1:0]      ack_seq,
    output logic                  nak_valid,
    output logic [SEQ_W-1:0]      nak_seq,
    output logic                  crc_error,
    output logic                  frame_drop,
    output logic [15:0]           frames_ok,
    output logic [15:0]           frames_dup
);
    logic                  chk_valid, chk_ready, chk_last;
    logic [DATA_WIDTH-1:0] chk_data;
    logic                  chk_crc_error, chk_crc_ok, chk_drop;

    axis_crc_checker #(
        .DATA_WIDTH(DATA_WIDTH), .CRC_WIDTH(CRC_WIDTH),
        .POLY(CRC_POLY), .INIT(CRC_INIT),
        .STORE_AND_FORWARD(1'b1), .MAX_FRAME_BEATS(MAX_FRAME_BEATS + 1)
    ) u_crc (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .s_axis_tvalid(s_axis_tvalid), .s_axis_tready(s_axis_tready),
        .s_axis_tdata(s_axis_tdata), .s_axis_tlast(s_axis_tlast),
        .s_axis_tuser(s_axis_tuser),
        .m_axis_tvalid(chk_valid), .m_axis_tready(chk_ready),
        .m_axis_tdata(chk_data), .m_axis_tlast(chk_last),
        .crc_error(chk_crc_error), .crc_ok(chk_crc_ok),
        .frame_drop(chk_drop)
    );

    typedef enum logic [1:0] { P_HDR, P_PAY, P_DROP } pstate_t;
    pstate_t pstate_q;

    logic [SEQ_W-1:0] expected_q, rx_seq_q, ack_seq_q;
    logic             dup_q;
    logic             ack_pulse_q, nak_pulse_q;
    logic [15:0]      ok_q, dup_cnt_q;

    assign m_axis_tvalid = enable && (pstate_q == P_PAY) && chk_valid;
    assign m_axis_tdata  = chk_data;
    assign m_axis_tlast  = chk_last;
    assign chk_ready     = (pstate_q == P_PAY) ? m_axis_tready : 1'b1;

    wire [SEQ_W:0] rx_dist = {1'b0, expected_q} - {1'b0, chk_data[SEQ_W-1:0]};
    wire           rx_dup  = (rx_dist != '0) && (rx_dist <= (SEQ_W+1)'(WINDOW));

    assign ack_valid  = ack_pulse_q;
    assign ack_seq    = ack_seq_q;
    assign nak_valid  = nak_pulse_q;
    assign nak_seq    = expected_q;
    assign crc_error  = chk_crc_error;
    assign frame_drop = chk_drop;
    assign frames_ok  = ok_q;
    assign frames_dup = dup_cnt_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pstate_q    <= P_HDR;
            expected_q  <= '0;
            rx_seq_q    <= '0;
            ack_seq_q   <= '0;
            dup_q       <= 1'b0;
            ok_q        <= '0;
            dup_cnt_q   <= '0;
            ack_pulse_q <= 1'b0;
            nak_pulse_q <= 1'b0;
        end else if (!enable) begin
            pstate_q    <= P_HDR;
            expected_q  <= '0;
            rx_seq_q    <= '0;
            dup_q       <= 1'b0;
            ok_q        <= '0;
            dup_cnt_q   <= '0;
            ack_pulse_q <= 1'b0;
            nak_pulse_q <= 1'b0;
        end else begin
            ack_pulse_q <= 1'b0;
            nak_pulse_q <= 1'b0;

            // A CRC-failed frame never reaches chk_valid.  NAK the frame we
            // are still waiting for as soon as the checker reports it.
            if (chk_crc_error)
                nak_pulse_q <= 1'b1;

            case (pstate_q)
                P_HDR: begin
                    if (chk_valid) begin
                        rx_seq_q <= chk_data[SEQ_W-1:0];
                        dup_q    <= rx_dup;
                        if (chk_last) begin
                            // Header-only frame: acknowledge or reject it.
                            if (chk_data[SEQ_W-1:0] == expected_q) begin
                                ack_pulse_q <= 1'b1;
                                ack_seq_q   <= chk_data[SEQ_W-1:0];
                                expected_q  <= expected_q + 1'b1;
                                ok_q        <= ok_q + 1'b1;
                            end else if (rx_dup) begin
                                ack_pulse_q <= 1'b1;
                                ack_seq_q   <= expected_q - 1'b1;
                                dup_cnt_q   <= dup_cnt_q + 1'b1;
                            end else begin
                                nak_pulse_q <= 1'b1;
                            end
                            pstate_q <= P_HDR;
                        end else begin
                            pstate_q <= (chk_data[SEQ_W-1:0] == expected_q)
                                        ? P_PAY : P_DROP;
                        end
                    end
                end
                P_PAY: begin
                    if (chk_valid && m_axis_tready) begin
                        if (chk_last) begin
                            ack_pulse_q <= 1'b1;
                            ack_seq_q   <= rx_seq_q;
                            expected_q  <= expected_q + 1'b1;
                            ok_q        <= ok_q + 1'b1;
                            pstate_q    <= P_HDR;
                        end
                    end
                end
                P_DROP: begin
                    if (chk_valid) begin
                        if (chk_last) begin
                            if (dup_q) begin
                                // Duplicate: re-ACK the last in-order frame so
                                // a lost ACK does not deadlock the sender.
                                ack_pulse_q <= 1'b1;
                                ack_seq_q   <= expected_q - 1'b1;
                                dup_cnt_q   <= dup_cnt_q + 1'b1;
                            end else begin
                                // Out of order: ask for the missing frame.
                                nak_pulse_q <= 1'b1;
                            end
                            pstate_q <= P_HDR;
                        end
                    end
                end
                default: pstate_q <= P_HDR;
            endcase
        end
    end

endmodule

/* verilator lint_on DECLFILENAME */
