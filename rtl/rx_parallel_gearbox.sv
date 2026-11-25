`timescale 1ns/1ps
`default_nettype none
// -----------------------------------------------------------------------------
// Quarter-rate RX gearbox: 4 bits per quarter-rate cycle -> DATA_W-bit words.
//
// Input: one 4-UI group per clk_q cycle.  Bit order inside a group is UI
// order: data_q[0] is the earliest UI and is pushed first (it becomes the
// lowest bit index of the word stream).
//
// Two assembly structures, selected by the CLK_FOLLOW parameter (constant --
// Yosys folds it, so only the selected path elaborates):
//
// CLK_FOLLOW = 1 (external-clock / Stage-B closure)
// -----------------------------------------------
// The breathing recovered clk_q absorbs the ppm drift continuously, so there
// are NO discrete content slips and the drop/insert repair path is not used.
// The gearbox is a FIXED 4 -> DATA_W nibble-segmented assembler:
//
//   * the output word is split into DATA_W/4 nibble segments;
//   * a nibble-phase counter (the recovered phase within the word) selects
//     the FIXED segment position for this cycle's 4 sampler bits;
//   * every clk_q cycle writes data_q[i] into segment pos, bit i -- constant
//     position math, constant-bounded loop, no variable-width push;
//   * the word completes when the phase counter wraps and is registered out.
//
// The word stream is bit-identical to the legacy shift-register assembly in
// this mode (data_q[0] earliest = lowest bit index, one word every
// DATA_W/4 cycles, word_valid one cycle after the completing push), so
// cdr_parallel_top and the TB boundary are unchanged.
//
// CLK_FOLLOW = 0 (fixed-clock rotator, verified Stage-A path)
// ----------------------------------------------------------
// The decimated loop reports the content-window shifts that the ppm drift
// and the rotation walk produce (see cdr_decimated_loop).  Each event; the
// gearbox holds the repair for the pipeline latency and then, at the shifted
// group's boundary:
//
//   slip_drop c > 0    the content revisited c bits (net sampling advance,
//                      data slower): skip the c leading bits of the group -
//                      the first c bits repeat the previous group's tail.
//   slip_insert c > 0  the content skipped c bits (net sampling delay, data
//                      faster): prepend c copies of the last streamed bit
//                      before the group - the same convention an elastic
//                      buffer uses for clock-tolerance compensation.
//
// Both repairs keep the streamed bit count matched to the local clk_q rate,
// so the word stream stays bit-continuous.  A real deployment funnels this
// into a proper elastic FIFO with a reclaimed read clock; for v1 the
// drop/insert is applied directly in the assembly shift register.
// -----------------------------------------------------------------------------
module rx_parallel_gearbox #(
    parameter int DATA_W = 16,           // multiple of 4 (16 or 32)
    // Must match the CDR loop / top mode.  1 = fixed 4->DATA_W segmented
    // assembly (no drop/insert).  0 = Stage-A repair path below.
    parameter bit CLK_FOLLOW = 1'b0
) (
    input  wire               clk_q,
    input  wire               rst_n,
    input  wire               enable,
    input  wire [3:0]         data_q,      // UI order, data_q[0] earliest
    input  wire               slip_evt,    // 1-cycle elastic event (mode 0 only)
    input  wire [1:0]         slip_drop,   // duplicated bits to drop
    input  wire [1:0]         slip_insert, // skipped bits to fill
    output reg  [DATA_W-1:0]  rx_word,
    output reg                word_valid
);

    localparam int BITS  = (DATA_W < 4) ? 4 : DATA_W;
    localparam int CNT_W = (BITS <= 1) ? 1 : $clog2(BITS);
    // Nibble segments per output word (fixed positions, CLK_FOLLOW mode).
    localparam int NIB     = (BITS < 4) ? 1 : BITS / 4;
    localparam int NIB_W   = (NIB <= 1) ? 1 : $clog2(NIB);

    // ------------------------------------------------------------------
    // CLK_FOLLOW = 1: fixed nibble-segmented 4 -> DATA_W assembly.
    // ------------------------------------------------------------------
    reg [BITS-1:0]  asm_q;      // word assembly register
    reg [NIB_W-1:0] nib_q;      // recovered phase inside the word (segment idx)

    wire nib_last = (nib_q == NIB_W'(NIB - 1));

    // ------------------------------------------------------------------
    // CLK_FOLLOW = 0: elastic repair path (Stage-A, unchanged).
    // ------------------------------------------------------------------
    reg [BITS-1:0]  shreg_q;
    reg [CNT_W-1:0] fill_q;
    reg             last_bit_q;
    reg [1:0]       pend_drop_q, pend_ins_q;
    reg [1:0]       pend_cnt_q;   // cycles until the shifted group arrives
    reg             pend_v_q;

    // Pending repair armed by the event, fired after PIPE_DLY cycles (the
    // shifted group reaches the push stage that many cycles later).
    localparam int PIPE_DLY = 3;
    wire arm   = enable && slip_evt && !CLK_FOLLOW;
    wire fire  = enable && pend_v_q && (pend_cnt_q == (PIPE_DLY-1));

    // Bits to push this cycle, stream order (index 0 first).
    reg [6:0] push_bits;
    reg [2:0] push_n;

    always_comb begin
        // Block-local temporaries so no module-scope signal latches.
        int d, n;
        d = 0;  n = 0;                  // comb temps: assign every path (no latch)
        push_n = (!CLK_FOLLOW && enable) ? 3'd4 : 3'd0;
        // Default the full 7-bit field so no element latches (the insert path
        // uses bits 4..6; the drop path leaves them at this default).
        push_bits = {3'b000, data_q};
        if (fire) begin
            if (pend_drop_q != 2'b00) begin
                d = pend_drop_q;            // 1..3 leading bits skipped
                // Constant-bounded loop (Yosys can't unroll n < 4-d): copy
                // conditionally so bits n >= 4-d keep the default data_q[n].
                for (n = 0; n < 4; n++)
                    if (n < 4 - d)
                        push_bits[n] = data_q[n + d];
                push_n = 3'd4 - pend_drop_q;
            end else if (pend_ins_q != 2'b00) begin
                d = pend_ins_q;             // 1..3 repeats prepended
                // Constant bound on n < d likewise.
                for (n = 0; n < 4; n++)
                    if (n < d)
                        push_bits[n] = last_bit_q;
                push_bits[d]   = data_q[0];
                push_bits[d+1] = data_q[1];
                push_bits[d+2] = data_q[2];
                push_bits[d+3] = data_q[3];
                push_n = 3'd4 + pend_ins_q;
            end
        end
    end

    always_ff @(posedge clk_q or negedge rst_n) begin
        if (!rst_n) begin
            asm_q      <= '0;
            nib_q      <= '0;
            shreg_q    <= '0;
            fill_q     <= '0;
            last_bit_q <= 1'b0;
            pend_drop_q<= 2'b00;
            pend_ins_q <= 2'b00;
            pend_cnt_q <= 2'b00;
            pend_v_q   <= 1'b0;
            rx_word    <= '0;
            word_valid <= 1'b0;
        end else if (CLK_FOLLOW) begin
            // Fixed assembly: this cycle's 4 sampler bits land in segment
            // nib_q at fixed bit offsets (segment n occupies bits 4n..4n+3,
            // data_q[0] = earliest UI = lowest bit of the segment).  Constant
            // bounds, constant position math -- fully synthesizable.
            word_valid <= 1'b0;
            if (enable) begin
                // Shadow with blocking assignments so the completing push is
                // included in the registered word (same-edge completion as
                // the legacy shift-register assembly).
                reg [BITS-1:0] asm_n;
                asm_n = asm_q;
                for (int i = 0; i < 4; i++)
                    asm_n[4*nib_q + i] = data_q[i];
                if (nib_last) begin
                    rx_word    <= asm_n;
                    word_valid <= 1'b1;
                    asm_q      <= '0;
                    nib_q      <= '0;
                end else begin
                    asm_q      <= asm_n;
                    nib_q      <= nib_q + 1'b1;
                end
            end
        end else begin
            word_valid <= 1'b0;
            // Arm / release the pending repair.
            if (pend_v_q && pend_cnt_q != 2'b11)
                pend_cnt_q <= pend_cnt_q + 2'b01;
            if (arm) begin
                pend_drop_q <= slip_drop;
                pend_ins_q  <= slip_insert;
                pend_cnt_q  <= 2'b00;
                pend_v_q    <= (slip_drop != 2'b00) || (slip_insert != 2'b00);
            end else if (fire) begin
                pend_v_q <= 1'b0;
            end
            if (push_n > 0) begin
                // Shadow the assembly register with blocking assignments so a
                // word completing mid-sequence sees this cycle's earlier bits.
                reg [BITS-1:0] shreg_n;
                int            c;
                shreg_n = shreg_q;
                c       = fill_q;
                for (int i = 0; i < 7; i++) begin  // push_n <= 7 (4+3 insert)
                    if (i < push_n) begin
                        if (c == BITS - 1) begin
                            shreg_n[BITS-1] = push_bits[i];
                            rx_word         <= shreg_n;
                            word_valid      <= 1'b1;
                            shreg_n         = '0;
                            c               = 0;
                        end else begin
                            shreg_n[c] = push_bits[i];
                            c          = c + 1;
                        end
                    end
                end
                shreg_q    <= shreg_n;
                fill_q     <= CNT_W'(c);
                last_bit_q <= push_bits[push_n-1];
            end
        end
    end

endmodule
