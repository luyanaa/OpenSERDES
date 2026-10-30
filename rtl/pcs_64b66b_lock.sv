`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// 64b/66b sync-header block-lock state machine.
//
// IEEE 802.3 clause 49 style block lock, but driven from a parallel 66-bit
// block stream rather than a raw serial bit-slip interface.  The deserializer
// is already aligned to a 66-bit boundary by an external mechanism (or the
// comma/block align produced upstream); this FSM decides whether the recovered
// block boundary is correct by observing the 2-bit sync headers.
//
// States:
//   LOCK_INIT : reset; not locked.
//   HUNT      : scan sync headers.  On a legal header (01 or 10) move to
//               PRESYNC; on an illegal header (00/11) stay in HUNT.
//   PRESYNC   : a candidate boundary.  Count consecutive good headers.
//               After GOOD_FOR_LOCK move to LOCK.  Any bad header returns to
//               HUNT (the candidate boundary was wrong).
//   LOCK      : block boundary confirmed; `block_lock` is asserted and the
//               decoded payload may be used.  Count bad headers in a sliding
//               window; if more than BAD_TO_UNLOCK occur within WIN bits the
//               boundary is presumed lost -> HUNT.
//
// `blk_valid` qualifies `sync_hdr` (one per recovered block).  Because the
// block boundary is external, we cannot slip bits here; a bad boundary simply
// prevents lock and, once locked, a sustained run of bad headers drops lock
// and returns to HUNT (the upstream align may then re-search).
// -----------------------------------------------------------------------------
module pcs_64b66b_lock #(
    // Good headers needed in PRESYNC to enter LOCK.
    parameter int GOOD_FOR_LOCK = 4,
    // Bad headers within the watch window that drop lock.
    parameter int BAD_TO_UNLOCK = 4,
    // Window (number of blocks) over which bad headers are tallied.
    parameter int WIN_BLOCKS    = 16
) (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       enable,
    input  logic       blk_valid,      // one pulse per recovered block
    input  logic [1:0] sync_hdr,       // block_in[65:64]
    output logic       block_lock,     // asserted while boundary is trusted
    output logic       presync,        // candidate boundary (not yet locked)
    output logic       bad_hdr         // pulse: illegal header seen this block
);

    typedef enum logic [1:0] {LOCK_INIT, HUNT, PRESYNC, LOCK} state_t;
    state_t state_q;

    localparam int GW = (GOOD_FOR_LOCK <= 1) ? 1 : $clog2(GOOD_FOR_LOCK + 1);
    // bad_q must be able to hold a count up to WIN_BLOCKS (it counts bad
    // headers within the whole window), and win_q counts to WIN_BLOCKS.
    localparam int BW = (WIN_BLOCKS <= 1) ? 1 : $clog2(WIN_BLOCKS + 1);
    localparam int WW = (WIN_BLOCKS <= 1) ? 1 : $clog2(WIN_BLOCKS + 1);

    logic [GW-1:0] good_q;
    logic [BW-1:0] bad_q;
    logic [WW-1:0] win_q;
    logic          hdr_ok;

    assign hdr_ok      = blk_valid && (sync_hdr == 2'b01 || sync_hdr == 2'b10);
    assign bad_hdr     = blk_valid && !hdr_ok;
    assign block_lock  = (state_q == LOCK);
    assign presync     = (state_q == PRESYNC);

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_q <= LOCK_INIT;
            good_q  <= '0;
            bad_q   <= '0;
            win_q   <= '0;
        end else if (!enable) begin
            state_q <= LOCK_INIT;
            good_q  <= '0;
            bad_q   <= '0;
            win_q   <= '0;
        end else if (blk_valid) begin
            case (state_q)
                LOCK_INIT: state_q <= HUNT;

                HUNT: begin
                    if (hdr_ok) begin
                        state_q <= PRESYNC;
                        good_q  <= GW'(1);
                    end
                end

                PRESYNC: begin
                    if (hdr_ok) begin
                        if (good_q >= GW'(GOOD_FOR_LOCK - 1)) begin
                            state_q <= LOCK;
                            bad_q   <= '0;
                            win_q   <= '0;
                        end else begin
                            good_q <= good_q + 1'b1;
                        end
                    end else begin
                        // Candidate boundary proved wrong.
                        state_q <= HUNT;
                        good_q  <= '0;
                    end
                end

                LOCK: begin
                    // Sliding window of bad headers.  win_q counts blocks in the
                    // current window; bad_q counts bad headers seen.  At the end
                    // of each window, if too many bad -> lose lock.
                    if (win_q >= WW'(WIN_BLOCKS - 1)) begin
                        if (bad_q + (hdr_ok ? '0 : 1'b1) > BW'(BAD_TO_UNLOCK))
                            state_q <= HUNT;
                        bad_q <= '0;
                        win_q <= '0;
                    end else begin
                        win_q <= win_q + 1'b1;
                        if (!hdr_ok && (bad_q < BW'(WIN_BLOCKS)))
                            bad_q <= bad_q + 1'b1;
                    end
                end

                default: state_q <= LOCK_INIT;
            endcase
        end
    end

endmodule
