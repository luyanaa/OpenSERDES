`timescale 1ns/1ps
`default_nettype none
/* verilator lint_off DECLFILENAME */
// File name kept as-is; the module inside is the PRBS31 word generator cell.

// -----------------------------------------------------------------------------
// Standalone 2 Gb/s-class PRBS31 TX macro (Stage A deliverable).
//
// Data path:  prbs31_word_gen (parallel PRBS front) -> tx_parallel_mux
//             (quarter-rate last stage) -> tx_driver (PMA wrapper).
//
// Why a parallel PRBS front instead of the sequential prbs_gen: prbs_gen
// emits one bit per clock; driving a 2 Gb/s stream from it would put the
// LFSR on a 2 GHz clock, i.e. the digital loop at line rate.  prbs31_word_gen
// advances the same x^31 + x^28 + 1 (PRBS31) LFSR by 4 steps per 500 MHz
// word clock and exposes the 4 successive MSBs as one nibble, so the digital
// loop runs at the word rate only.  The bit order matches prbs_gen's
// MSB-of-SEED-first convention.
//
// Ports (standalone: own clocking, nothing wired into the SoC wrapper):
//   clk_word  : 500 MHz nibble/word clock (digital loop domain)
//   clk_ph[3:0]: ideal 500 MHz phases, 90 deg apart, driving the final MUX.
//                Stage A keeps these as ideal inputs; the real PLL/DCO macro
//                is out of scope and slots in later behind the same pins.
//   serial_out: 2 Gb/s serial stream (through the tx_driver wrapper)
//   data_valid: clk_word-domain flag; asserts on the clk_word edge that
//               loads the first valid PRBS nibble -- serial data starts at
//               the following clk_ph[0] edge.
//   nibble_dbg: observation tap of the parallel nibble into the final MUX.
//
// Clock relationships (see tx_parallel_mux header for the full contract):
//   clk_word leads clk_ph[0] by 250 ps; clk_ph[k] leads clk_ph[k+1] by 500 ps.
//
// Future generator hookup (not wired now): gen_ip.py would grow a
// `rate_mode: quarter` option that emits this macro's RTL + SDC clock groups
// as the TX side of a quarter-rate lane; the 4 phase-clock pins would then be
// bound to the clock-macro outputs at the wrapper level.
// -----------------------------------------------------------------------------

// Parallel PRBS31 front: 4 bits per word clock, LSB-first (nibble[0] first).
module prbs31_word_gen #(
    parameter logic [30:0] SEED = 31'h7FFF_FFFF
) (
    input  wire        clk_word,
    input  wire        rst_n,
    input  wire        enable,
    input  wire        clear,
    output logic [3:0] nibble,
    output logic       valid
);
    logic [30:0] state_q;

    // Advance the LFSR four steps (combinational shift of the same feedback
    // polynomial prbs_gen uses: state_q[30] ^ state_q[27]).
    function [30:0] adv4;
        input [30:0] s;
        integer i;
        reg [30:0] t;
        begin
            t = s;
            for (i = 0; i < 4; i = i + 1)
                t = {t[29:0], t[30] ^ t[27]};
            adv4 = t;
        end
    endfunction

    // The four bits serialized next, LSB-first: nibble[0] leaves first on
    // ph0.  First out is the current MSB (prbs_gen convention), then the MSBs
    // of the 1-, 2- and 3-step-advanced states (= state_q[29], [28], [27]).
    assign nibble = {state_q[27], state_q[28], state_q[29], state_q[30]};

    always_ff @(posedge clk_word or negedge rst_n) begin
        if (!rst_n) begin
            state_q <= SEED;
            valid   <= 1'b0;
        end else if (clear) begin
            state_q <= SEED;
            valid   <= 1'b0;
        end else if (enable) begin
            state_q <= adv4(state_q);
            valid   <= 1'b1;
        end
    end

endmodule

// Stage B (real-clock option): USE_REAL_CLOCK=1 swaps the external ideal
// clk_ph ports for the on-macro real multiphase clock, pma/tx_clk4.sv ->
// PLL_MULTIPHASE_DCO (the signed-off cdr_ihp130 block, STEP=3 ~500 MHz
// class).  The macro stays standalone either way -- nothing is wired into
// the SoC wrapper.  dco_en gates the ring; in the ideal-clock mode it is
// unused (appended last so existing Stage-A instantiations still elaborate).
module tx_prbs31_macro #(
    parameter logic [30:0] PRBS_SEED    = 31'h7FFF_FFFF,
    parameter bit          USE_REAL_CLOCK = 1'b0, // 1: internal tx_clk4/PLL_MULTIPHASE_DCO
    parameter integer      TX_STEP        = 3,    // DCO STEP (freq centering)
    parameter integer      TX_CLOSE_LOAD  = 1,    // DCO closing-node load trim
    parameter bit          TX_TAP_PAIR    = 1'b1  // tx_clk4 USE_TAP_PAIR
) (
    input  wire        clk_word,      // 500 MHz word clock (digital loop)
    input  wire [3:0]  clk_ph,        // ideal 500 MHz phases, 90 deg apart
                                       // (unused when USE_REAL_CLOCK=1)
    input  wire        rst_n,
    input  wire        enable,        // 0: LFSR holds, stream repeats last bits
    input  wire        clear,         // reseed the LFSR
    input  wire        dco_en,        // ring enable (USE_REAL_CLOCK=1 only)
    output logic       serial_out,    // 2 Gb/s serial stream
    output logic       data_valid,
    output logic [3:0] nibble_dbg
);
    logic [3:0] nib;
    logic       dv;
    logic       ser_mux;

    // Serializer phase clocks: external ideal set, or the real DCO phases.
    // ph_ser is the observation point for the TB (hierarchical ref).
    wire [3:0] ph_ser;
    generate
        if (USE_REAL_CLOCK) begin : g_real_clk
            tx_clk4 #(
                .STEP        (TX_STEP),
                .CLOSE_LOAD  (TX_CLOSE_LOAD),
                .USE_TAP_PAIR(TX_TAP_PAIR)
            ) u_clk4 (
                .DCO_EN   (dco_en),
                .clk_ph   (ph_ser),
                .dco_out  (),
                .dco_out_n()
            );
        end else begin : g_ext_clk
            assign ph_ser = clk_ph;
        end
    endgenerate

    prbs31_word_gen #(.SEED(PRBS_SEED)) u_prbs (
        .clk_word (clk_word),
        .rst_n    (rst_n),
        .enable   (enable),
        .clear    (clear),
        .nibble   (nib),
        .valid    (dv)
    );

    tx_parallel_mux #(
        .LSB_FIRST    (1'b1),
        .RETIME_INPUT (1'b1)
    ) u_mux (
        .clk_word  (clk_word),
        .clk_ph    (ph_ser),
        .rst_n     (rst_n),
        .enable    (1'b1),
        .nibble_in (nib),
        .serial_out(ser_mux)
    );

    tx_driver u_driver (
        .IN (ser_mux),
        .OUT(serial_out)
    );

    assign data_valid = dv;
    assign nibble_dbg = nib;

endmodule

`default_nettype wire
