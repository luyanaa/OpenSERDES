`timescale 1ns/1ps

/* verilator lint_off DECLFILENAME */

// -----------------------------------------------------------------------------
// Variable-length AXIS frame <-> fixed WORDS-beat internal frame.
//
// The serializer/deserializer link hands over exactly WORDS beats per frame
// (axis_tx_adapter contract).  SoC traffic is variable length, so the
// packetizer slices one input frame into one or more fixed-size fragments:
//
//   internal beat 0      : {last_frag, 2'b00, frag_len[4:0]}
//   internal beats 1..N  : payload bytes, zero padded past frag_len
//   internal frame       : always WORDS beats, tlast on beat WORDS-1
//
// frag_len is 1..WORDS-1 (WORDS=1 leaves no room for a header and is rejected
// at elaboration).  Fragments of one input frame are emitted back-to-back;
// input frames are never interleaved.  tready backpressure in the middle of a
// frame is ordinary AXIS stalling and cannot lose a beat, so the only framing
// error the slicer can see is a zero-length frame (tlast on the first beat):
// that is flagged on frame_error, and the frame is emitted as one payload byte
// so the internal frame contract still holds.
//
// The depacketizer is the inverse and is intended for the RX AXIS side: it
// strips the header, emits frag_len bytes, asserts tlast on the final byte of
// the last fragment, and flags malformed headers (reserved bits, frag_len out
// of range, missing tlast) without emitting them.
// -----------------------------------------------------------------------------

module axis_frame_packetizer #(
    parameter int DATA_WIDTH = 8,
    parameter int WORDS      = 4
) (
    input  logic                          clk,
    input  logic                          rst_n,
    input  logic                          clear,
    input  logic                          s_axis_tvalid,
    output logic                          s_axis_tready,
    input  logic [DATA_WIDTH-1:0]         s_axis_tdata,
    input  logic                          s_axis_tlast,
    output logic                          m_axis_tvalid,
    input  logic                          m_axis_tready,
    output logic [DATA_WIDTH-1:0]         m_axis_tdata,
    output logic                          m_axis_tlast,
    output logic                          m_axis_tuser,
    output logic                          frame_error
);
    localparam int PAYLOAD = WORDS - 1;
    localparam int BW      = (WORDS <= 1) ? 1 : $clog2(WORDS);
    localparam logic [BW-1:0] LAST_BEAT = BW'(WORDS-1);

    typedef enum logic [1:0] { P_IDLE, P_FILL, P_EMIT } pstate_t;
    pstate_t pstate_q;

    logic [WORDS-1:0][DATA_WIDTH-1:0] buf_q;
    logic [BW-1:0] cnt_q;        // payload bytes collected in this fragment
    logic [BW-1:0] emit_q;
    logic          last_q;
    logic          err_q;

    logic finish;                // this accepted beat completes the fragment
    logic accept;

    assign accept         = s_axis_tvalid & s_axis_tready;
    assign s_axis_tready  = (pstate_q == P_IDLE) || (pstate_q == P_FILL);
    assign finish         = accept &&
                            (s_axis_tlast || (cnt_q == BW'(PAYLOAD-1)));
    assign m_axis_tvalid  = (pstate_q == P_EMIT);
    assign m_axis_tdata   = buf_q[emit_q];
    assign m_axis_tlast   = (emit_q == LAST_BEAT);
    assign m_axis_tuser   = last_q;
    assign frame_error    = err_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pstate_q <= P_IDLE;
            buf_q    <= '0;
            cnt_q    <= '0;
            emit_q   <= '0;
            last_q   <= 1'b0;
            err_q    <= 1'b0;
        end else if (clear) begin
            pstate_q <= P_IDLE;
            cnt_q    <= '0;
            emit_q   <= '0;
            last_q   <= 1'b0;
            err_q    <= 1'b0;
        end else begin
            case (pstate_q)
                P_IDLE, P_FILL: begin
                    if (accept) begin
                        // frame_error is a per-frame level: refreshed on the
                        // first beat, held for the rest of the frame.  Only a
                        // zero-length frame is detectable here.
                        if (cnt_q == '0)
                            err_q <= s_axis_tlast;
                        // Slot 0 is the header, payload starts at slot 1.
                        buf_q[cnt_q + 1'b1] <= s_axis_tdata;
                        cnt_q <= cnt_q + 1'b1;
                        if (finish) begin
                            buf_q[0] <= '0;
                            buf_q[0][7:0] <= {s_axis_tlast, 2'b00,
                                              5'(cnt_q + 1'b1)};
                            // Zero the padding slots so the internal frame is
                            // deterministic.
                            for (int b = 0; b < WORDS; b++) begin
                                if (b > int'(cnt_q) + 1)
                                    buf_q[b] <= '0;
                            end
                            last_q   <= s_axis_tlast;
                            emit_q   <= '0;
                            pstate_q <= P_EMIT;
                        end else begin
                            pstate_q <= P_FILL;
                        end
                    end
                end
                P_EMIT: begin
                    if (m_axis_tready) begin
                        if (emit_q == LAST_BEAT) begin
                            cnt_q    <= '0;
                            pstate_q <= P_IDLE;
                        end else begin
                            emit_q <= emit_q + 1'b1;
                        end
                    end
                end
                default: pstate_q <= P_IDLE;
            endcase
        end
    end

endmodule


module axis_frame_depacketizer #(
    parameter int DATA_WIDTH = 8,
    parameter int WORDS      = 4
) (
    input  logic                          clk,
    input  logic                          rst_n,
    input  logic                          clear,
    input  logic                          s_axis_tvalid,
    output logic                          s_axis_tready,
    input  logic [DATA_WIDTH-1:0]         s_axis_tdata,
    input  logic                          s_axis_tlast,
    output logic                          m_axis_tvalid,
    input  logic                          m_axis_tready,
    output logic [DATA_WIDTH-1:0]         m_axis_tdata,
    output logic                          m_axis_tlast,
    output logic                          frame_error
);
    localparam int PAYLOAD  = WORDS - 1;
    localparam int BW       = (WORDS <= 1) ? 1 : $clog2(WORDS);
    localparam logic [BW-1:0] LAST_PAY  = BW'(WORDS-2);

    typedef enum logic [1:0] { D_IDLE, D_PAY, D_EMIT } dstate_t;
    dstate_t dstate_q;

    logic [WORDS-1:0][DATA_WIDTH-1:0] frag_q;
    logic [BW-1:0] beat_q;
    logic [BW-1:0] emit_q;
    logic [4:0]    len_q;
    logic          last_q;
    logic          drop_q;
    logic          err_q;

    logic hdr_ok;
    logic [4:0] len_in;
    logic       last_in;

    assign s_axis_tready = (dstate_q == D_IDLE) || (dstate_q == D_PAY);
    assign m_axis_tvalid = (dstate_q == D_EMIT);
    assign m_axis_tdata  = frag_q[emit_q];
    assign m_axis_tlast  = last_q && (5'(emit_q) == len_q - 1'b1);
    assign frame_error   = err_q;

    assign len_in  = s_axis_tdata[4:0];
    assign last_in = s_axis_tdata[7];
    assign hdr_ok  = (s_axis_tdata[6:5] == 2'b00) &&
                     (len_in != 5'd0) && (len_in <= 5'(PAYLOAD));

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            dstate_q <= D_IDLE;
            frag_q   <= '0;
            beat_q   <= '0;
            emit_q   <= '0;
            len_q    <= '0;
            last_q   <= 1'b0;
            drop_q   <= 1'b0;
            err_q    <= 1'b0;
        end else if (clear) begin
            dstate_q <= D_IDLE;
            beat_q   <= '0;
            emit_q   <= '0;
            drop_q   <= 1'b0;
            err_q    <= 1'b0;
        end else begin
            case (dstate_q)
                D_IDLE: begin
                    if (s_axis_tvalid) begin
                        len_q    <= len_in;
                        last_q   <= last_in;
                        drop_q   <= ~hdr_ok;
                        beat_q   <= '0;
                        emit_q   <= '0;
                        err_q    <= err_q | ~hdr_ok;
                        dstate_q <= D_PAY;
                    end
                end
                D_PAY: begin
                    if (s_axis_tvalid) begin
                        if (!drop_q)
                            frag_q[beat_q] <= s_axis_tdata;
                        beat_q <= beat_q + 1'b1;
                        if (beat_q == LAST_PAY) begin
                            if (!s_axis_tlast) begin
                                err_q <= 1'b1;
                                dstate_q <= D_IDLE;
                            end else if (drop_q) begin
                                dstate_q <= D_IDLE;
                            end else begin
                                dstate_q <= D_EMIT;
                            end
                        end
                    end
                end
                D_EMIT: begin
                    if (m_axis_tready) begin
                        if (5'(emit_q) == len_q - 1'b1)
                            dstate_q <= D_IDLE;
                        else
                            emit_q <= emit_q + 1'b1;
                    end
                end
                default: dstate_q <= D_IDLE;
            endcase
        end
    end

endmodule

/* verilator lint_on DECLFILENAME */
