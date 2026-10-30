`timescale 1ns/1ps
/* verilator lint_off DECLFILENAME */

// -----------------------------------------------------------------------------
// Minimal 64b/66b data-block coding.
//
// 66-bit block: {2-bit sync header, 64-bit scrambled payload}.
//   sync = 2'b01 for a data block.
// The self-synchronous scrambler uses G(x) = 1 + x^39 + x^58:
//   y[k] = x[k] ^ y[k-39] ^ y[k-58]
// and the decoder applies the same recurrence to the received block.
//
// This is a data-block primitive only.  It does not implement the 64b/66b
// control-block formats, block distribution, gearbox, or the full IEEE PCS.
// In particular, `coding: 64b66b` in the generator remains fail-closed until
// those layers are integrated and verified.
// -----------------------------------------------------------------------------
module pcs_64b66b_encoder (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        advance,
    input  logic [63:0] data_in,
    input  logic        ctrl,
    output logic [65:0] block_out,
    output logic        sync_ok
);
    // Self-synchronous scrambler G(x)=1+x^39+x^58.  The 58-bit LFSR state is
    // persistent across consecutive 64-bit payloads (real 64b/66b behaviour),
    // and is only re-seeded on reset.  `advance` clocks one block through.
    logic [57:0] state_q;
    logic [57:0] state_c;
    logic        y;
    logic [63:0] scrambled;

    always_comb begin
        state_c   = state_q;
        scrambled = '0;
        for (int i = 0; i < 64; i++) begin
            y = data_in[i] ^ state_c[38] ^ state_c[57];
            scrambled[i] = y;
            state_c = {state_c[56:0], y};
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)      state_q <= '0;
        else if (advance) state_q <= state_c;
    end

    assign block_out = {ctrl ? 2'b10 : 2'b01, scrambled};
    assign sync_ok = 1'b1;
endmodule

module pcs_64b66b_decoder (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        advance,
    input  logic [65:0] block_in,
    output logic [63:0] data_out,
    output logic        sync_ok,
    output logic        ctrl_block
);
    // Descrambler uses the same self-synchronous recurrence but its shift
    // register is loaded with the *received* scrambled bits, so it locks to the
    // encoder state after 58 bits without any explicit seed exchange.
    logic [57:0] state_q;
    logic [57:0] state_c;
    logic        d;
    logic [63:0] descrambled;

    always_comb begin
        state_c    = state_q;
        descrambled = '0;
        for (int i = 0; i < 64; i++) begin
            d = block_in[i] ^ state_c[38] ^ state_c[57];
            descrambled[i] = d;
            state_c = {state_c[56:0], block_in[i]};
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)      state_q <= '0;
        else if (advance) state_q <= state_c;
    end

    assign data_out  = descrambled;
    assign sync_ok   = (block_in[65:64] == 2'b01) || (block_in[65:64] == 2'b10);
    assign ctrl_block = (block_in[65:64] == 2'b10);
endmodule
/* verilator lint_on DECLFILENAME */

// -----------------------------------------------------------------------------
// 66-to-64-bit gearbox.
//
// Packs a stream of 66-bit PCS blocks into 64-bit words.  The buffer holds up
// to two words of data; `block_ready` deasserts while the buffer must drain.
// This is a datapath primitive; block distribution and PCS lane alignment are
// separate layers.
// -----------------------------------------------------------------------------
module pcs_64b66b_gearbox (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        enable,
    input  logic [65:0] block_in,
    input  logic        block_valid,
    output logic        block_ready,
    output logic [63:0] word_out,
    output logic        word_valid,
    input  logic        word_ready
);
    // Continuous bit reservoir.  `count_q` is the number of valid bits in
    // `buf_q`, which are always right-aligned at the low end (LSB-first).  A
    // new 66-bit block is appended at bit offset `count_q`; when at least 64
    // bits are buffered the oldest 64 (buf_q[63:0]) are popped to the output
    // register and the remainder is shifted down.  The buffer is 130 bits so
    // it can hold 64 (pending word) + 66 (incoming block) bits without loss.
    //
    // Bit order: block_in[0] is the earliest bit, so after packing, word_out[0]
    // is the first bit of the first block received.
    logic [129:0] buf_q;
    logic [7:0]   count_q;      // 0..130
    logic [63:0]  word_q;
    logic         valid_q;

    logic        pop_now;
    assign pop_now     = !valid_q && (count_q >= 8'd64);
    // After a pop this cycle the buffer holds count_q-64 bits; a block may be
    // accepted if it then fits in the remaining space (or if no pop, fits
    // directly).  This keeps every accepted bit inside the 130-bit buffer.
    assign block_ready = enable &&
                         (pop_now ? (count_q - 8'd64 + 8'd66 <= 8'd130)
                                  : (count_q + 8'd66 <= 8'd130));
    assign word_out    = word_q;
    assign word_valid  = valid_q;

    logic [129:0] buf_c;
    logic [7:0]   count_c;

    always_comb begin
        buf_c   = buf_q;
        count_c = count_q;
        if (pop_now) begin
            buf_c   = {64'b0, buf_q[129:64]};
            count_c = count_q - 8'd64;
        end
        if (block_valid && block_ready) begin
            buf_c[count_c +: 66] = block_in;
            count_c = count_c + 8'd66;
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            buf_q   <= '0;
            count_q <= '0;
            word_q  <= '0;
            valid_q <= 1'b0;
        end else if (!enable) begin
            buf_q   <= '0;
            count_q <= '0;
            word_q  <= '0;
            valid_q <= 1'b0;
        end else begin
            buf_q   <= buf_c;
            count_q <= count_c;
            if (valid_q && word_ready)
                valid_q <= 1'b0;
            if (pop_now) begin
                word_q  <= buf_q[63:0];
                valid_q <= 1'b1;
            end
        end
    end
endmodule
