`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Full-rate sampler boundary -> half-rate pair former for the RX endpoint.
//
// The front sampler (or source-synchronous receiver_sampler) delivers one bit
// per clk_bit together with bit_valid.  This block groups two consecutive valid
// bits into one pair and presents it on clk_word (= clk_bit / 2), so the wide
// parallel bank never toggles at the bit rate.
//
// Handshake: the bit domain holds the last completed pair (pair_bit0/1) and
// toggles pair_seq.  The word domain samples the held pair and compares the
// sequence bit with its own copy; a change means a new pair.  This does not
// depend on a one-cycle pulse landing inside a particular clk_word window, so
// an alignment-induced phase shift cannot drop a pair.
//
// Frame alignment: `align` is a clk_bit pulse that marks the last bit of the
// sync pattern (comma_align drives it combinationally).  That bit is discarded
// and the next valid bit becomes bit0 of a fresh pair, so the following pair is
// word0 bits 0-1.  pair_align is asserted together with that first pair; the
// deserializer uses it to load word0 directly instead of appending.
//
// Clock contract: clk_word must be the divide-by-2 clock of clk_bit from the
// same source.  bit_valid is expected once per clk_bit while the CDR is
// locked; a gapped bit_valid stretches the pair, which is legal but must be
// accounted for by the frame timeout in the integrator.
// -----------------------------------------------------------------------------
module rx_pair_align (
    input  logic clk_bit,
    input  logic clk_word,
    input  logic rst_n,
    input  logic enable,
    input  logic align,
    input  logic bit_in,
    input  logic bit_valid,
    output logic pair_valid,
    output logic pair_align,
    output logic pair_bit0,
    output logic pair_bit1
);

    logic phase_q;
    logic b0_q;
    logic held0_q;
    logic held1_q;
    logic pair_seq_q;
    logic pair_align_q;
    logic pending_align_q;

    // Bit domain: collect two valid bits, remembering whether the pair starts
    // a new frame.  The completed pair is held until the next pair replaces it.
    always_ff @(posedge clk_bit or negedge rst_n) begin
        if (!rst_n) begin
            phase_q         <= 1'b0;
            b0_q            <= 1'b0;
            held0_q         <= 1'b0;
            held1_q         <= 1'b0;
            pair_seq_q      <= 1'b0;
            pair_align_q    <= 1'b0;
            pending_align_q <= 1'b0;
        end else if (align) begin
            // Drop the pattern's last bit and restart on bit0.
            phase_q         <= 1'b0;
            pending_align_q <= 1'b1;
        end else if (enable && bit_valid) begin
            if (!phase_q) begin
                b0_q    <= bit_in;
                phase_q <= 1'b1;
            end else begin
                // Latch the completed pair into holding registers: b0_q is
                // overwritten by the next pair one bit later, so the word
                // domain must not sample it directly.
                held0_q         <= b0_q;
                held1_q         <= bit_in;
                phase_q         <= 1'b0;
                pair_seq_q      <= ~pair_seq_q;
                pair_align_q    <= pending_align_q;
                pending_align_q <= 1'b0;
            end
        end
    end

    // Word domain: a sequence change means the held pair is new.
    logic pair_seq_seen_q;

    always_ff @(posedge clk_word or negedge rst_n) begin
        if (!rst_n) begin
            pair_seq_seen_q <= 1'b0;
            pair_valid      <= 1'b0;
            pair_align      <= 1'b0;
            pair_bit0       <= 1'b0;
            pair_bit1       <= 1'b0;
        end else begin
            pair_seq_seen_q <= pair_seq_q;
            pair_valid      <= (pair_seq_q != pair_seq_seen_q);
            if (pair_seq_q != pair_seq_seen_q) begin
                pair_bit0  <= held0_q;
                pair_bit1  <= held1_q;
                pair_align <= pair_align_q;
            end else begin
                pair_align <= 1'b0;
            end
        end
    end

endmodule
