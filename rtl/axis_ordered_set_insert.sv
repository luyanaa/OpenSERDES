`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Insert one ordered-set word in front of every internal frame.
//
// `serdes_soc_cdc_wrapper` carries `comma (0xBC) + WORDS payload` per internal
// frame and `axis_tx_adapter` requires tlast on the last beat.  A variable-length
// framer (axis_frame_packetizer) emits the fragments only, so the comma has to be
// put back.  This module is that thin layer, and nothing else:
//
//   - a comma word is presented before the first payload beat of every frame;
//   - the comma is consumed from the framer independently of the payload
//     handshake, so it never blocks the fragment stream;
//   - tlast is forwarded on the payload beat that carried it;
//   - the comma is flushed by `clear`, so an APB clear cannot leave a half
//     frame in the pipe.
//
// Frame boundary detection is deliberately narrow: `LAST_ACCEPTED` (the module
// saw the framer's tlast) arms the next comma.  Nothing here looks at
// `tvalid` edges, so a continuously-valid fragment stream works.
// -----------------------------------------------------------------------------
module axis_ordered_set_insert #(
    parameter int DATA_WIDTH = 8,
    parameter logic [DATA_WIDTH-1:0] SET_WORD = 'hBC
) (
    input  logic              clk,
    input  logic              rst_n,
    input  logic              clear,
    input  logic              enable,
    // From the framer.
    input  logic              s_tvalid,
    output logic              s_tready,
    input  logic [DATA_WIDTH-1:0] s_tdata,
    input  logic              s_tlast,
    // To the SerDes frame port.
    output logic              m_tvalid,
    input  logic              m_tready,
    output logic [DATA_WIDTH-1:0] m_tdata,
    output logic              m_tlast,
    // Number of ordered sets inserted, for telemetry.
    output logic [15:0]       set_count
);
    logic comma_pending_q;   // a set must be sent before the next payload beat
    logic [15:0] count_q;

    assign m_tvalid = enable & (comma_pending_q | s_tvalid);
    assign m_tdata  = comma_pending_q ? SET_WORD : s_tdata;
    assign m_tlast  = comma_pending_q ? 1'b0 : s_tlast;
    // The set is taken from here, not from the framer.
    assign s_tready = enable & m_tready & ~comma_pending_q;

    assign set_count = count_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            comma_pending_q <= 1'b1;      // first frame needs a set too
            count_q         <= '0;
        end else if (clear) begin
            comma_pending_q <= 1'b1;
        end else if (!enable) begin
            comma_pending_q <= 1'b1;
        end else if (m_tvalid && m_tready) begin
            if (comma_pending_q) begin
                comma_pending_q <= 1'b0;
                count_q         <= count_q + 1'b1;
            end else if (s_tlast) begin
                // Next frame starts after this one.
                comma_pending_q <= 1'b1;
            end
        end
    end

endmodule
