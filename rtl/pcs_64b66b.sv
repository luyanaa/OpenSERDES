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
    input  logic [63:0] data_in,
    input  logic        ctrl = 1'b0,
    output logic [65:0] block_out,
    output logic        sync_ok
);
    logic [57:0] state;
    logic        y;
    logic [63:0] scrambled;

    always_comb begin
        state    = '0;
        scrambled = '0;
        for (int i = 0; i < 64; i++) begin
            y = data_in[i] ^ state[38] ^ state[57];
            scrambled[i] = y;
            state = {state[56:0], y};
        end
    end

    assign block_out = {ctrl ? 2'b10 : 2'b01, scrambled};
    assign sync_ok = 1'b1;
endmodule

module pcs_64b66b_decoder (
    input  logic [65:0] block_in,
    output logic [63:0] data_out,
    output logic        sync_ok,
    output logic        ctrl_block
);
    logic [57:0] state;
    logic        d;
    logic [63:0] descrambled;

    always_comb begin
        state = '0;
        descrambled = '0;
        for (int i = 0; i < 64; i++) begin
            d = block_in[i] ^ state[38] ^ state[57];
            descrambled[i] = d;
            state = {state[56:0], block_in[i]};
        end
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
    logic [129:0] buf_q;
    logic [7:0]   count_q;
    logic [63:0]  word_q;
    logic         valid_q;

    assign block_ready = enable && !valid_q && (count_q < 8'd64);
    assign word_out    = word_q;
    assign word_valid  = valid_q;

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
            if (valid_q && word_ready)
                valid_q <= 1'b0;

            if (!valid_q && count_q >= 8'd64) begin
                word_q  <= buf_q[63:0];
                buf_q   <= buf_q >> 64;
                count_q <= count_q - 8'd64;
                valid_q <= 1'b1;
            end else if (block_valid && block_ready) begin
                buf_q   <= {block_in, buf_q[63:0]};
                count_q <= count_q + 8'd66;
            end
        end
    end
endmodule
