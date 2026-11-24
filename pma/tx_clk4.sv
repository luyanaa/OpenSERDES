`timescale 1ns/1ps
`default_nettype none
// -----------------------------------------------------------------------------
// tx_clk4 -- 4-phase TX serializer clock from the real multiphase DCO
// (Stage B, TX real-clock hookup).
//
// Replaces the ideal clk_ph[3:0] sources of Stage A with the REAL free-running
// multiphase ring: PLL_MULTIPHASE_DCO (adpll_gen, the signed-off cdr_ihp130
// block, STEP=3 / ~500 MHz class; ~335 MHz @ SS, ~480-600 MHz @ TT/FF).
//
// Even-phase mapping (Stage-B brief) vs the measured quadrature
// ------------------------------------------------------
// PH_W=3 -> PHASE[7:0], 8 taps of the in-ring delay line, tap k at exactly
// k*STEP stages after DCO_OUT.  The Stage-B brief mandates the EVEN taps:
//
//     clk_ph[0] = PHASE[0]   clk_ph[1] = PHASE[2]
//     clk_ph[2] = PHASE[4]   clk_ph[3] = PHASE[6]
//
// Measured on the travelling-wave ring this set is 41.5 deg apart, NOT 90
// (see structural reality check below), so it is NOT mux-usable; it is kept
// as the USE_TAP_PAIR=0 option only for traceability of the brief.  The
// DEFAULT mapping (USE_TAP_PAIR=1) is the measured quadrature:
//
//     clk_ph[0] =  PHASE[0]  clk_ph[1] =  PHASE[4]
//     clk_ph[2] = ~PHASE[0]  clk_ph[3] = ~PHASE[4]
//
// PHASE order MUST match tx_parallel_mux's clk_ph[0..3] = 0/90/180/270 deg
// convention: clk_ph[k] leads clk_ph[k+1] by one serial UI (a quarter of the
// word period, 500 ps at 2 Gb/s), clk_ph[0] being the earliest.  A tap-order
// mistake rotates the serialized nibble -- see tb/tb_tx_prbs31_pma.sv.
//
// Structural reality check (re-measured 2026-11-25 against the TRAVELLING-
// WAVE pll_multiphase_dco, AND2X1 closure, in tb mode TX_CLK4_STRUCT with
// 40 ps/stage timed cells)
// ------------------------------------------------------------
// The upstream fix (commit 865ef20: inverting NAND -> NON-inverting AND2X1
// closure) makes the loop inversion count exactly NSTAGE = NPHASE*STEP+1
// (ODD) -> clean travelling wave.  Measured on the new ring:
//   * Period T = 2*(NSTAGE+1)*stage_delay = 2*26*40 ps = 2.080 ns (481 MHz
//     in the TT-like timed model; upstream post-route STA: ~331 MHz @ SS,
//     tap uniformity 19.5 ps).  The ring oscillates from DCO_EN release at
//     ANY timing (early and 20-ns-late release both verified) -- the old
//     even-inversion latch risk is resolved.
//   * Tap grid: PHASE[k] edges sit k*STEP stages after DCO_OUT (STEP*stage
//     = 120 ps apart), so the 8 taps span NPHASE*STEP = 24 stages of the
//     52-stage period -- a HALF-period grid at ~T/17.3 spacing, with the
//     complementary (+T/2) edges completing the UI grid.
//   * Even-tap mapping clk_ph[i] = PHASE[2i]: taps 2*STEP = 6 stages apart
//     vs the T/4 = 13 stages a 90-degree set needs -> measured
//     0/41.5/83.1/124.6 deg, NOT 0/90/180/270.  NOT usable for the mux;
//     kept only as the documented Stage-B-brief mapping (TB flags it).
//   * Correct quadrature from this ring: one tap pair + complementary T/2
//     edges: PHASE[0], PHASE[4], ~PHASE[0], ~PHASE[4] = measured
//     0/83.1/180.0/263.1 deg -- 90 deg with a -1-tap-stage (40 ps @sim)
//     systematic error on the two tap-derived phases, T/2 edges exact.
//     USE_TAP_PAIR=1 (now the DEFAULT) selects it; inter-phase gaps
//     480/560/480/560 ps (spread 80 ps in the behavioral timed model).
//   * Exact 0/90/180/270 needs tap spacing (NSTAGE+1)/4 = 6.5 stages --
//     impossible with integer STEP; an upstream geometry with a 4*STEP-cell
//     loop (NSTAGE = 4*STEP-1 inverters + AND, taps at 0/STEP/2*STEP/3*STEP)
//     would make PHASE[0]/PHASE[2] + complements EXACTLY 0/90/180/270.
//     Flag to the adpll_gen owner; meanwhile re-measure quadrant error on
//     the routed macro (adpll_gen flow/macro/cdr_ihp130) at PnR.
//   * Sim X-wave note: the initial X on chain[NSTAGE] is injected into the
//     loop if DCO_EN rises while that node is still X, and circulates (vvp
//     zero-init artifact; defined edges stay on schedule).  Release after
//     the first traversal (26*stage_delay) for an X-free timed sim.
//
// Behavioral sim mode
// -------------------
// The stdcell models used in digital sim are ZERO-DELAY, so the ring cannot
// self-oscillate in iverilog (same conclusion as adpll_gen's
// pll_multiphase_dco_tb.v, which substitutes a timed structural model).
// `ifdef TX_CLK4_BEHAVIORAL swaps in a #delay behavioral 4-phase generator at
// a parameterizable rate (PERIOD_NS) so the existing TBs still measure
// correctness; the synthesizable DCO path is exercised separately by
// compiling with timed PLL_CELL_* models (see tb/tb_tx_prbs31_pma.sv).
//
// The macro stays standalone: nothing here is wired into the SoC wrapper.
// -----------------------------------------------------------------------------
module tx_clk4 #(
    // PLL_MULTIPHASE_DCO ring parameters (frequency centering).
    parameter integer STEP       = 3,     // 3 = ~500 MHz class (25-stage ring)
    parameter integer CLOSE_LOAD = 1,     // static closing-node load trim
    // Behavioral mode only: clock period (500 MHz default) and the time of
    // the first clk_ph[0] posedge (0.5 ns matches the Stage-A TB convention:
    // clk_word posedges lead clk_ph[0] by 250 ps).
    parameter real    PERIOD_NS  = 2.0,
    parameter real    PH0_T_NS   = 0.5,
    // Real mode only: 0 = documented even-tap mapping PHASE[{0,2,4,6}];
    // 1 = physically 90-degree set PHASE[0]/PHASE[4] + complementary edges
    // (see structural reality check above).
    parameter bit     USE_TAP_PAIR = 1'b1
) (
    input  wire        DCO_EN,            // ring enable (real mode)
    output logic [3:0] clk_ph,            // 0/90/180/270 deg, tx_parallel_mux order
    output wire        dco_out,           // ring output (observation/PI tap)
    output wire        dco_out_n
);

`ifdef TX_CLK4_BEHAVIORAL
    // -------------------------------------------------------------------
    // Sim-only: four ideal free-running 50% duty generators, posedges
    // PERIOD_NS/4 apart in tx_parallel_mux phase order.  DCO_EN is ignored
    // (gating/enable is handled by the macro's digital enable).
    // -------------------------------------------------------------------
    localparam real QTR  = PERIOD_NS / 4.0;
    localparam real HALF = PERIOD_NS / 2.0;

    initial begin
        clk_ph[0] = 1'b0;
        #(PH0_T_NS);
        forever begin clk_ph[0] = 1'b1; #HALF; clk_ph[0] = 1'b0; #HALF; end
    end
    initial begin
        clk_ph[1] = 1'b0;
        #(PH0_T_NS +   QTR);
        forever begin clk_ph[1] = 1'b1; #HALF; clk_ph[1] = 1'b0; #HALF; end
    end
    initial begin
        clk_ph[2] = 1'b0;
        #(PH0_T_NS + 2*QTR);
        forever begin clk_ph[2] = 1'b1; #HALF; clk_ph[2] = 1'b0; #HALF; end
    end
    initial begin
        clk_ph[3] = 1'b0;
        #(PH0_T_NS + 3*QTR);
        forever begin clk_ph[3] = 1'b1; #HALF; clk_ph[3] = 1'b0; #HALF; end
    end

    assign dco_out   = clk_ph[0];   // stand-in ring outputs for observers
    assign dco_out_n = ~clk_ph[0];

`else
    // -------------------------------------------------------------------
    // Real path: PLL_MULTIPHASE_DCO (PH_W=3 -> 8 phases), even taps out.
    // PDK cells stay behind the PLL_CELL_* wrappers inside the DCO; this
    // module adds no PDK-specific instantiation of its own.
    // -------------------------------------------------------------------
    wire [7:0] PHASE;

    PLL_MULTIPHASE_DCO #(
        .PH_W      (3),
        .DELAY_BITS(0),
        .STEP      (STEP),
        .CLOSE_LOAD(CLOSE_LOAD)
    ) u_dco (
        .DCO_EN   (DCO_EN),
        .DCO_OUT  (dco_out),
        .DCO_OUT_N(dco_out_n),
        .PHASE    (PHASE)
    );

    generate
        if (USE_TAP_PAIR) begin : g_tap_pair
            // Physically nearest 90-degree (quadrature) set from the ring
            // geometry: taps 4 slots apart (12 stages; T/4 = 13 stages on the
            // 26-cell traversal) plus their complementary T/2 edges, which a
            // 50%-duty tap provides with no extra delay element.
            assign clk_ph[0] =  PHASE[0];   //   0 deg
            assign clk_ph[1] =  PHASE[4];   // ~90 deg ( -1 tap stage )
            assign clk_ph[2] = ~PHASE[0];   // 180 deg (T/2 edge)
            assign clk_ph[3] = ~PHASE[4];   // ~270 deg ( -1 tap stage )
        end else begin : g_even_taps
            // Documented mapping: even taps only.  Order is the
            // tx_parallel_mux convention clk_ph[0..3] = 0/90/180/270.
            assign clk_ph[0] = PHASE[0];
            assign clk_ph[1] = PHASE[2];
            assign clk_ph[2] = PHASE[4];
            assign clk_ph[3] = PHASE[6];
        end
    endgenerate
`endif

endmodule

`default_nettype wire
