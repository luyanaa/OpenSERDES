`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Half-rate TX endpoint wrapper (generator top for tx.rate_mode: half).
//
// The parallel capture and word bookkeeping run on clk_word; the final bit
// selection runs on clk_bit = 2 * clk_word.  Both clocks must come from the
// same source with the divide-by-2 phase relationship enforced in the SDC.
//
// Reset: asynchronous active-low, asserted to both domains at once (the
// prototype uses one reset).  The integrator is responsible for asynchronous
// assert and for the clock-phase relationship; this wrapper deliberately does
// not hide that contract behind a synchronizer.
//
// Scope: target tx only.  There is no half-rate RX (the sampler/CDR side is a
// separate, unverified prototype), so the generator rejects rate_mode: half
// for target link/rx.
// -----------------------------------------------------------------------------
module serdes_tx_half_rate_digital #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8,
    parameter bit LSB_FIRST  = 1'b1
) (
    input  logic                        clk_word,
    input  logic                        clk_bit,
    input  logic                        rst_n,
    input  logic                        in_valid,
    output logic                        in_ready,
    input  logic [DATA_WIDTH-1:0]       in_word,
    output logic                        serial_out,
    output logic                        serial_valid,
    output logic                        busy,
    output logic                        frame_done
);

    serializer_half_rate #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST)
    ) u_ser (
        .clk_word(clk_word),
        .clk_bit(clk_bit),
        .rst_n(rst_n),
        .in_valid(in_valid),
        .in_ready(in_ready),
        .in_word(in_word),
        .serial_out(serial_out),
        .serial_valid(serial_valid),
        .busy(busy),
        .frame_done(frame_done)
    );

endmodule
