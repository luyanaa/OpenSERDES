`timescale 1ns/1ps

// AXI4-Stream CRC checker for the frame format produced by
// axis_crc_inserter: payload beats terminated by a tuser flag (payload last),
// then ceil(CRC_WIDTH/DATA_WIDTH) CRC beats with tlast on the final CRC beat.
//
// Two modes, selected by STORE_AND_FORWARD:
//
//   0  streaming detect (default).  Payload beats are forwarded downstream
//      immediately; CRC beats are consumed and compared.  crc_error pulses
//      after the frame ends, so payload already delivered cannot be recalled.
//      Cheap: no storage.  Suitable for link diagnostics.
//
//   1  store-and-forward.  Payload beats are buffered in a MAX_FRAME_BEATS-deep
//      RAM while the CRC is computed; the CRC beats are still consumed for the
//      check.  Only when the CRC verifies is the buffered frame replayed to
//      m_axis (tlast on the final beat).  A bad frame is dropped entirely.
//      This gives true error-frame isolation at the cost of the buffer RAM and
//      a full frame of latency.  Frames longer than MAX_FRAME_BEATS overflow:
//      the frame is dropped and s_axis is stalled until it ends.
module axis_crc_checker #(
    parameter int  DATA_WIDTH = 8,
    parameter int  CRC_WIDTH  = 16,
    parameter logic [CRC_WIDTH-1:0] POLY = 16'h1021,
    parameter logic [CRC_WIDTH-1:0] INIT = '1,
    parameter bit  LSB_FIRST  = 1'b0,
    parameter bit  REFLECT_OUT = 1'b0,
    parameter logic [CRC_WIDTH-1:0] XOR_OUT = '0,
    parameter bit  STORE_AND_FORWARD = 1'b0,
    // Payload beats buffered in store-and-forward mode.  Size to the largest
    // frame (in beats) the link can carry; a frame that exceeds it is dropped.
    parameter int  MAX_FRAME_BEATS = 256
) (
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic                    enable,
    input  logic                    s_axis_tvalid,
    output logic                    s_axis_tready,
    input  logic [DATA_WIDTH-1:0]   s_axis_tdata,
    input  logic                    s_axis_tlast,
    input  logic                    s_axis_tuser,
    output logic                    m_axis_tvalid,
    input  logic                    m_axis_tready,
    output logic [DATA_WIDTH-1:0]   m_axis_tdata,
    output logic                    m_axis_tlast,
    output logic                    crc_error,
    output logic                    crc_ok,
    // High in store-and-forward mode when a frame exceeded MAX_FRAME_BEATS and
    // was dropped (sticky until cleared by enable low / reset).
    output logic                    frame_drop
);

    localparam int CRC_BYTES = (CRC_WIDTH + DATA_WIDTH - 1) / DATA_WIDTH;
    localparam int IDX_W = (CRC_BYTES <= 1) ? 1 : $clog2(CRC_BYTES);

    logic [IDX_W-1:0]     crc_idx_q;
    logic                 first_q;
    logic                 state_crc_q;
    logic                 s_fire;
    logic                 crc_start;
    logic                 crc_end;
    logic                 crc_data_valid;
    logic [CRC_WIDTH-1:0] crc_comb;
    logic [CRC_WIDTH-1:0] expected_q;
    logic                 error_q;
    logic                 ok_q;

    /* verilator lint_off PINCONNECTEMPTY */
    crc_frame #(
        .DATA_WIDTH(DATA_WIDTH), .CRC_WIDTH(CRC_WIDTH), .POLY(POLY),
        .INIT(INIT), .LSB_FIRST(LSB_FIRST),
        .REFLECT_OUT(REFLECT_OUT), .XOR_OUT(XOR_OUT)
    ) u_crc (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .frame_start(crc_start), .frame_end(crc_end),
        .data_valid(crc_data_valid), .data(s_axis_tdata),
        .crc(crc_comb), .crc_raw(), .crc_valid()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    assign s_fire         = s_axis_tvalid && s_axis_tready;
    assign crc_start      = s_fire && first_q && !state_crc_q;
    assign crc_end        = s_fire && s_axis_tuser && !state_crc_q;
    assign crc_data_valid = s_fire && !state_crc_q;
    assign crc_error      = error_q;
    assign crc_ok         = ok_q;

    generate
    if (!STORE_AND_FORWARD) begin : g_stream
        // ------------------------- streaming detect -------------------------
        assign s_axis_tready = enable && (!state_crc_q ? m_axis_tready : 1'b1);
        assign m_axis_tvalid = enable && !state_crc_q && s_axis_tvalid;
        assign m_axis_tdata  = s_axis_tdata;
        assign m_axis_tlast  = s_axis_tuser && !state_crc_q;
        assign frame_drop    = 1'b0;

        always_ff @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                crc_idx_q   <= '0;
                first_q     <= 1'b1;
                state_crc_q <= 1'b0;
                expected_q  <= '0;
                error_q     <= 1'b0;
                ok_q        <= 1'b0;
            end else if (!enable) begin
                crc_idx_q   <= '0;
                first_q     <= 1'b1;
                state_crc_q <= 1'b0;
                expected_q  <= '0;
                error_q     <= 1'b0;
                ok_q        <= 1'b0;
            end else begin
                error_q <= 1'b0;
                ok_q    <= 1'b0;
                if (!state_crc_q && s_fire) begin
                    first_q <= 1'b0;
                    if (s_axis_tuser) begin
                        state_crc_q <= 1'b1;
                        crc_idx_q   <= '0;
                        expected_q  <= '0;
                    end
                end else if (state_crc_q && s_fire) begin
                    expected_q <= expected_q |
                                  (CRC_WIDTH'(s_axis_tdata) << (crc_idx_q * DATA_WIDTH));
                    if (s_axis_tlast || (crc_idx_q == IDX_W'(CRC_BYTES - 1))) begin
                        if ((expected_q |
                             (CRC_WIDTH'(s_axis_tdata) << (crc_idx_q * DATA_WIDTH))) != crc_comb)
                            error_q <= 1'b1;
                        else
                            ok_q <= 1'b1;
                        state_crc_q <= 1'b0;
                        first_q     <= 1'b1;
                        crc_idx_q   <= '0;
                    end else begin
                        crc_idx_q <= crc_idx_q + 1'b1;
                    end
                end
            end
        end
    end else begin : g_saf
        // ------------------------ store-and-forward -------------------------
        localparam int AW = (MAX_FRAME_BEATS <= 1) ? 1 : $clog2(MAX_FRAME_BEATS);

        // Frame buffer write/read pointers (one extra bit so a full count of
        // MAX_FRAME_BEATS is representable) and the pending-frame good flag.
        logic [AW:0]          wr_ptr_q;
        logic [AW:0]          rd_ptr_q;
        logic [AW:0]          frame_len_q;
        logic                 frame_good_q;   // buffered frame CRC passed
        logic                 draining_q;     // replaying buffered payload
        logic                 overflow_q;     // frame too long -> drop
        logic                 drop_q;
        logic [DATA_WIDTH-1:0] buf_q      [MAX_FRAME_BEATS];
        logic                  buf_last_q [MAX_FRAME_BEATS];

        // During a payload beat we accept into the buffer (unless overflow).
        // During CRC beats we always consume.  While draining a good frame we
        // cannot take a new frame, so s_axis is stalled.
        wire   payload_beat = !state_crc_q;
        wire   buf_room     = (wr_ptr_q < (AW+1)'(MAX_FRAME_BEATS));
        // Consume payload beats while there is room OR while dropping an
        // over-length frame (so upstream can finish the frame); consume CRC
        // beats unconditionally.
        assign s_axis_tready = enable && !draining_q &&
                               (payload_beat ? (buf_room || overflow_q)
                                             : 1'b1);

        // Replay path: emit buffered payload beats while draining.
        assign m_axis_tvalid = enable && draining_q;
        assign m_axis_tdata  = buf_q[rd_ptr_q[AW-1:0]];
        assign m_axis_tlast  = buf_last_q[rd_ptr_q[AW-1:0]] &&
                               (rd_ptr_q == frame_len_q - 1'b1);
        assign frame_drop    = drop_q;

        wire m_fire = m_axis_tvalid && m_axis_tready;

        always_ff @(posedge clk or negedge rst_n) begin
            if (!rst_n) begin
                crc_idx_q    <= '0;
                first_q      <= 1'b1;
                state_crc_q  <= 1'b0;
                expected_q   <= '0;
                error_q      <= 1'b0;
                ok_q         <= 1'b0;
                wr_ptr_q     <= '0;
                rd_ptr_q     <= '0;
                frame_len_q  <= '0;
                frame_good_q <= 1'b0;
                draining_q   <= 1'b0;
                overflow_q   <= 1'b0;
                drop_q       <= 1'b0;
            end else if (!enable) begin
                crc_idx_q    <= '0;
                first_q      <= 1'b1;
                state_crc_q  <= 1'b0;
                expected_q   <= '0;
                error_q      <= 1'b0;
                ok_q         <= 1'b0;
                wr_ptr_q     <= '0;
                rd_ptr_q     <= '0;
                frame_len_q  <= '0;
                frame_good_q <= 1'b0;
                draining_q   <= 1'b0;
                overflow_q   <= 1'b0;
                drop_q       <= 1'b0;
            end else begin
                error_q <= 1'b0;
                ok_q    <= 1'b0;
                drop_q  <= 1'b0;

                // Replay the buffered frame one beat per m_fire.
                if (draining_q && m_fire) begin
                    if (rd_ptr_q == frame_len_q - 1'b1) begin
                        draining_q <= 1'b0;
                        rd_ptr_q   <= '0;
                    end else begin
                        rd_ptr_q <= rd_ptr_q + 1'b1;
                    end
                end

                if (payload_beat && s_fire) begin
                    first_q <= 1'b0;
                    if (!overflow_q) begin
                        // Buffer the payload beat while it still fits.
                        buf_q[wr_ptr_q[AW-1:0]]      <= s_axis_tdata;
                        buf_last_q[wr_ptr_q[AW-1:0]] <= s_axis_tuser;
                        wr_ptr_q <= wr_ptr_q + 1'b1;
                        // If this write just filled the last slot and the frame
                        // is not over yet, the next beat will overflow.
                        if ((wr_ptr_q + 1'b1 == (AW+1)'(MAX_FRAME_BEATS)) && !s_axis_tuser)
                            overflow_q <= 1'b1;
                    end
                    if (s_axis_tuser) begin
                        // End of payload -> move to CRC collection.
                        state_crc_q <= 1'b1;
                        crc_idx_q   <= '0;
                        expected_q  <= '0;
                        frame_len_q <= overflow_q ? '0 : (wr_ptr_q + 1'b1);
                    end
                end else if (state_crc_q && s_fire) begin
                    expected_q <= expected_q |
                                  (CRC_WIDTH'(s_axis_tdata) << (crc_idx_q * DATA_WIDTH));
                    if (s_axis_tlast || (crc_idx_q == IDX_W'(CRC_BYTES - 1))) begin
                        logic [CRC_WIDTH-1:0] rx_crc;
                        rx_crc = expected_q |
                                 (CRC_WIDTH'(s_axis_tdata) << (crc_idx_q * DATA_WIDTH));
                        if (overflow_q) begin
                            // Too long: drop, count as a dropped frame.
                            drop_q     <= 1'b1;
                            overflow_q <= 1'b0;
                        end else if (rx_crc != crc_comb) begin
                            error_q <= 1'b1;   // bad frame dropped (not replayed)
                        end else begin
                            ok_q         <= 1'b1;
                            frame_good_q <= 1'b1;
                            draining_q   <= 1'b1;   // release buffered frame
                        end
                        state_crc_q <= 1'b0;
                        first_q     <= 1'b1;
                        crc_idx_q   <= '0;
                        wr_ptr_q    <= '0;
                    end else begin
                        crc_idx_q <= crc_idx_q + 1'b1;
                    end
                end
            end
        end
    end
    endgenerate

endmodule
