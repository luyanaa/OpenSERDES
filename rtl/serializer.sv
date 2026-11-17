`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Abstract, technology-independent serializer.
//
// Frame format:
//   - WORDS words of DATA_WIDTH bits, total RATIO = WORDS * DATA_WIDTH bits.
//   - One serial bit per enabled clock.
//   - LSB_FIRST=1: word0 bit0 first, then word0 bit1 ... word0 bit31, word1 bit0...
//   - LSB_FIRST=0: word0 bit31 first ... word0 bit0, word1 bit31...
//
// Handshake:
//   - ready is high when the block is idle.
//   - load && ready captures the complete packed array.
//   - serial_valid is high while serial_out carries a valid bit.
//   - frame_done pulses for one clock after the last bit has been accepted.
//
// CAPTURE_MODE:
//   - 0: single-cycle capture; one enable fans out to all RATIO input flops.
//   - 1: one word per clock using a one-hot capture select.  ready is low for
//        WORDS cycles, and the external parallel source must hold the frame
//        stable.  Capture flops are enabled in groups of four words, and the
//        per-word select still gates the flops inside the active group.
//
// WORD_ONEHOT:
//   - 0: binary word counter.
//   - 1: one-hot word select.  Bit order, frame length and reset are unchanged.
//
// There are no derived clocks; all state advances only when enable is high.
// -----------------------------------------------------------------------------
module serializer #(
    parameter int DATA_WIDTH   = 32,
    parameter int WORDS        = 8,
    parameter bit LSB_FIRST    = 1'b1,
    parameter bit CAPTURE_MODE = 1'b0,
    // 0: binary word counter.  1: one-hot word select.  The frame order,
    // the bit order and the cycle count stay the same; only the select
    // network changes.
    parameter bit WORD_ONEHOT  = 1'b0
) (
    input  logic                              clk,
    input  logic                              rst_n,
    input  logic                              enable,
    input  logic                              load,
    input  logic [WORDS-1:0][DATA_WIDTH-1:0]  par_data,
    output logic                              ready,
    output logic                              serial_out,
    output logic                              serial_valid,
    output logic                              busy,
    output logic                              frame_done
);

    localparam int BIT_W    = (DATA_WIDTH <= 1) ? 1 : $clog2(DATA_WIDTH);
    localparam int WORD_W   = (WORDS <= 1) ? 1 : $clog2(WORDS);
    localparam logic [BIT_W-1:0]  LAST_BIT  = BIT_W'(DATA_WIDTH-1);
    localparam logic [WORD_W-1:0] LAST_WORD = WORD_W'(WORDS-1);

    logic [WORDS-1:0][DATA_WIDTH-1:0] data_q;
    logic [WORDS-1:0]                 word_bit;
    logic [BIT_W-1:0]                 bit_sel_q;
    logic [WORD_W-1:0]                word_sel_q;
    logic [WORDS-1:0]                 word_oh_q;
    logic                             active_q;
    logic                             capture_q;
    logic                             serial_mux;
    logic                             frame_done_q;

    assign ready        = ~(active_q | capture_q) & rst_n;
    assign serial_valid = active_q;
    assign busy         = active_q | capture_q;
    assign frame_done   = frame_done_q;
    assign serial_out   = serial_mux;

    // One bit-select slice per word, with operand isolation: when the block is
    // not driving valid serial data the wide bit-select muxes are held at 0 so
    // they stop toggling with bit_sel_q.  serial_out is a don't-care when
    // serial_valid (active_q) is low, so this is bit-exact.
    generate
        for (genvar w = 0; w < WORDS; w++) begin : g_word_bit
            assign word_bit[w] = active_q & (LSB_FIRST
                ? data_q[w][bit_sel_q]
                : data_q[w][LAST_BIT-bit_sel_q]);
        end
    endgenerate

    // Word select.  Counter: equality against word_sel_q.  One-hot: the
    // select bit itself.  Only the selected network is updated, so the other
    // register is unread and drops out of the mapped netlist.
    wire last_word = WORD_ONEHOT ? word_oh_q[WORDS-1] : (word_sel_q == LAST_WORD);
    always_comb begin
        serial_mux = 1'b0;
        for (int w = 0; w < WORDS; w++) begin
            if (WORD_ONEHOT ? word_oh_q[w] : (word_sel_q == WORD_W'(w)))
                serial_mux = word_bit[w];
        end
    end

    generate
        if (CAPTURE_MODE) begin : g_capture
            logic [WORDS-1:0] cap_sel_q;
            // Local enable groups.  cap_grp_q is one bit per GROUP_W words,
            // so a capture pulse reaches one group instead of every word
            // decoder.  The per-word cap_sel_q bit still gates the flops, so
            // a word outside the active group cannot capture.
            localparam int GROUP_W = 4;
            localparam int NGROUP  = (WORDS + GROUP_W - 1) / GROUP_W;
            logic [NGROUP-1:0] cap_grp_q;
            for (genvar w = 0; w < WORDS; w++) begin : g_word_capture
                always_ff @(posedge clk or negedge rst_n) begin
                    if (!rst_n)
                        data_q[w] <= '0;
                    else if (capture_q && cap_grp_q[w / GROUP_W] && cap_sel_q[w])
                        data_q[w] <= par_data[w];
                end
            end

            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    bit_sel_q    <= '0;
                    word_sel_q   <= '0;
                    word_oh_q    <= '0;
                    cap_sel_q    <= '0;
                    cap_grp_q    <= '0;
                    active_q     <= 1'b0;
                    capture_q    <= 1'b0;
                    frame_done_q <= 1'b0;
                end else begin
                    frame_done_q <= 1'b0;

                    if (capture_q) begin
                        if (cap_sel_q[WORDS-1]) begin
                            capture_q  <= 1'b0;
                            active_q   <= 1'b1;
                            bit_sel_q  <= '0;
                            word_sel_q <= '0;
                            word_oh_q  <= {{(WORDS-1){1'b0}}, 1'b1};
                            cap_sel_q  <= '0;
                            cap_grp_q  <= '0;
                        end else begin
                            // Logical shift avoids the WORDS=1 part-select
                            // cap_sel_q[-1:0] that breaks elaboration.
                            cap_sel_q <= cap_sel_q << 1;
                            // Advance the group enable when the one-hot
                            // select crosses a group boundary.  The index is
                            // a constant per generated copy, so this is a
                            // shift of a one-hot, not a decoder of cap_sel_q.
                            for (int g = 0; g < NGROUP - 1; g++) begin
                                if (cap_sel_q[(g + 1) * GROUP_W - 1])
                                    cap_grp_q <= cap_grp_q << 1;
                            end
                        end
                    end else if (!active_q) begin
                        if (load && ready) begin
                            capture_q <= 1'b1;
                            cap_sel_q <= '0;
                            cap_sel_q[0] <= 1'b1;
                            cap_grp_q <= '0;
                            cap_grp_q[0] <= 1'b1;
                        end
                    end else if (enable) begin
                        if (last_word && bit_sel_q == LAST_BIT) begin
                            active_q     <= 1'b0;
                            frame_done_q <= 1'b1;
                        end else if (bit_sel_q == LAST_BIT) begin
                            bit_sel_q <= '0;
                            if (WORD_ONEHOT)
                                word_oh_q <= word_oh_q << 1;
                            else
                                word_sel_q <= word_sel_q + 1'b1;
                        end else begin
                            bit_sel_q <= bit_sel_q + 1'b1;
                        end
                    end
                end
            end
        end else begin : g_single
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    data_q       <= '0;
                    bit_sel_q    <= '0;
                    word_sel_q   <= '0;
                    word_oh_q    <= '0;
                    active_q     <= 1'b0;
                    capture_q    <= 1'b0;
                    frame_done_q <= 1'b0;
                end else begin
                    frame_done_q <= 1'b0;

                    if (!active_q) begin
                        if (load && ready) begin
                            data_q     <= par_data;
                            bit_sel_q  <= '0;
                            word_sel_q <= '0;
                            word_oh_q  <= {{(WORDS-1){1'b0}}, 1'b1};
                            active_q   <= 1'b1;
                        end
                    end else if (enable) begin
                        if (last_word && bit_sel_q == LAST_BIT) begin
                            active_q     <= 1'b0;
                            frame_done_q <= 1'b1;
                        end else if (bit_sel_q == LAST_BIT) begin
                            bit_sel_q <= '0;
                            if (WORD_ONEHOT)
                                word_oh_q <= word_oh_q << 1;
                            else
                                word_sel_q <= word_sel_q + 1'b1;
                        end else begin
                            bit_sel_q <= bit_sel_q + 1'b1;
                        end
                    end
                end
            end
        end
    endgenerate

endmodule
