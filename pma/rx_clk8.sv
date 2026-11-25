`timescale 1ns/1fs
`default_nettype none
// -----------------------------------------------------------------------------
// rx_clk8 -- Stage-B RX sampling-clock source: the REAL multiphase DCO ring as
// the oscillating reference + a pi_code/pi_frac controlled sampling-phase
// generator (the behavioral stand-in for the silicon phase interpolator).
//
// Replaces the ideal clk_ph[7:0] / clk_q sources of Stage A with
//
//     PLL_MULTIPHASE_DCO (adpll_gen, travelling-wave ring, PH_W=3/STEP=3,
//     the same signed-off cdr_ihp130 block pma/tx_clk4 uses on the TX side)
//     + the CDR loop's phase word re-timing the 8 sampler clocks.
//
// Abstraction level (honest statement)
// -----------------------------------
// * REAL: the ring.  In structural mode (default here) PLL_MULTIPHASE_DCO is
//   elaborated with TIMED PLL_CELL_* stand-ins (the PDK cells are zero-delay
//   in digital sim, so the ring cannot self-oscillate; same approach as
//   tb_tx_prbs31_pma TX_CLK4_STRUCT).  It oscillates at T = 2*(NSTAGE+1)*t_cell
//   and its PHASE[7:0] taps are real ring nodes.
// * BEHAVIORAL: the phase interpolator.  The raw ring taps span a HALF period
//   in STEP-stage increments (~2.08 ns period, taps ~120 ps apart at 40 ps
//   cells) -- they do NOT give the UI/8-resolution sampler phases.  A real PI
//   (coarse tap mux + fine interpolating delay line between two adjacent
//   taps) is a later physical block.  Here the PI is a sim model:
//
//       phase = {pi_code, pi_frac} * (PERIOD / 2048)   (mod PERIOD)
//
//   i.e. the loop's 11-bit phase word r_q = {tap[2:0], frac[7:0]} IS the
//   sampling phase code, LSB ~0.977 ps at 2 ns, full scale one 500 MHz clock
//   (4 UI).  The word is applied as a CONTINUOUS phase: every LSB increment
//   moves every sampling instant by one LSB, and a frac->tap carry is just a
//   representation rollover with no phase discontinuity.  This is exactly how
//   a real interpolator behaves and it is the mapping consistent with the
//   loop's CLK_FOLLOW content bookkeeping (one-tap-offset index, see
//   cdr_decimated_loop).
//
//   DEVIATION from the mapping sketched in tb_cdr_parallel.sv's Stage-B
//   comment (tap[2:1] = 0.5 ns steps, tap[0] dead, frac LSB 0.25/256 ns): that
//   sketch is discontinuous -- at every frac->tap[0] carry the phase SNAPS
//   BACK 0.25 ns (frac full scale is half a coarse step), a real content
//   revisit the loop's f bookkeeping reports half a tap-walk cycle later.  The
//   continuous map above has no snap: carries are invisible, and the physical
//   content shifts (data taps crossing bit boundaries) land within ~20 LSBs
//   (~20 ps) of the tap[2:1] crossings the loop reports.
//
// Clocking contract (CLK_FOLLOW=1 semantics of cdr_decimated_loop)
// ---------------------------------------------------------------
//   clk_q      posedges at t = phase (mod PERIOD); the quarter-rate group
//              boundary follows the re-timed sampling phase ("clk_q
//              follows").  At a nonzero ppm offset the loop's frac field
//              walks continuously at the drift rate, so clk_q BREATHES
//              (period PERIOD +- LSB steps) and the group content stays 4
//              consecutive data UIs -- the ppm elastic closure is the
//              continuously re-timed clock itself, no discrete drop/insert
//              events (the gearbox slip-repair path belongs to the fixed-
//              clock rotator mode, CLK_FOLLOW=0).
//   clk_ph[k]  posedges at phase + PH_OFF + 0.25*k ns (mod PERIOD): data taps
//              (even k) sit PH_OFF = 0.1875 ns into their UI, edge taps (odd
//              k) 0.4375 ns in, 0.0625 ns before the next boundary -- the
//              Stage-A sampler_bank layout, unchanged.
//
// Structural mode: the ring PACES the groups (one clean DCO_OUT posedge
// triggers one group; the first group is anchored at ring-edge + phase) and
// the group start then advances delta-wise (t_g += PERIOD + d).  Anchoring
// every group directly at edge + (phase word mod PERIOD) is discontinuous
// at the word's mod-2 ns wrap (the anchor jumps a full period = a +-4 UI
// content shift), so the continuous delta walk is the honest representation;
// the ring's own period error accumulates as a slowly drifting offset
// between t_g and the ring edges (~2 fs/period with the trimmed timed
// cells).  The stand-in cell delay is chosen so the trimmed ring lands on
// the nominal period (see tb/tb_rx_parallel_pma.sv); a free-running
// un-trimmed ring (+4% period at 40 ps cells) would exceed the loop's
// tracking budget -- in silicon the ring is frequency-trimmed (CLOSE_LOAD /
// tuning codes) and the PI is a real interpolator that re-centers
// continuously.
// -----------------------------------------------------------------------------
module rx_clk8 #(
    parameter real PERIOD_NS  = 2.0,    // quarter-rate clock period (4 UI)
    parameter real START_T_NS = 1.0,    // behavioral mode: first group start
    parameter real PH_OFF_NS  = 0.1875  // data tap offset after group start
) (
    input  wire        DCO_EN,          // ring enable (structural mode)
    input  wire [2:0]  pi_code,         // CDR coarse phase word (tap field)
    input  wire [7:0]  pi_frac,         // CDR fractional phase word
    output logic       clk_q,           // quarter-rate loop clock (follows)
    output logic [7:0] clk_ph,          // 8 sampling phases, 45 deg apart
    output wire        dco_out,         // ring output (observation / anchor)
    output wire        dco_out_n
);

    // One tap:frac LSB = PERIOD/2048 ~= 0.977 ps; the 11-bit word spans 4 UI.
    localparam real PH_LSB_NS = PERIOD_NS / 2048.0;

`ifdef RX_CLK8_BEHAVIORAL
    // Behavioral mode: no DCO instance (the TB compiles without
    // pll_multiphase_dco.v).  Observation ports become stand-ins.
    assign dco_out   = clk_q;
    assign dco_out_n = ~clk_q;
`else
    // Structural mode: the REAL travelling-wave ring.  PDK cells stay behind
    // the PLL_CELL_* wrappers inside the DCO; this module instantiates no PDK
    // cells of its own.  Compile with timed PLL_CELL_* stand-ins (see TB).
    wire       ring_out;
    wire [7:0] ring_phase;   // raw ring taps: real, but ~120 ps apart (half
                             // period in STEP stages) -- too coarse for the
                             // UI/8 sampler grid; observed, not muxed.
    assign dco_out = ring_out;

    PLL_MULTIPHASE_DCO #(
        .PH_W      (3),
        .DELAY_BITS(0),
        .STEP      (3),
        .CLOSE_LOAD(1)
    ) u_dco (
        .DCO_EN   (DCO_EN),
        .DCO_OUT  (ring_out),
        .DCO_OUT_N(dco_out_n),
        .PHASE    (ring_phase)
    );
`endif

    // ------------------------------------------------------------------
    // Phase code -> sampling phase.  The whole loop phase word
    // {pi_code, pi_frac} is the phase: continuous, LSB ~0.977 ps, mod 2 ns.
    // ------------------------------------------------------------------
    function automatic real ph_ns(input real code);
        begin
            ph_ns = code * PH_LSB_NS;
        end
    endfunction

    // Wrap a phase delta into (-PERIOD/2, PERIOD/2]: single window steps of
    // the loop are <= (FMAX + kick) LSBs ~ 160 LSBs ~ 0.16 ns, far below half
    // a period, so this is only the mod-2 ns renumbering of the word.
    function automatic real wrap_d(input real d);
        begin
            while (d >  PERIOD_NS / 2.0) d = d - PERIOD_NS;
            while (d <= -PERIOD_NS / 2.0) d = d + PERIOD_NS;
            wrap_d = d;
        end
    endfunction

    // Applied state (published one cycle ahead; a phase change landing on a
    // clk_q edge takes effect from the NEXT group, which is also what the
    // gearbox pipeline latency expects).
    real ph_cur;          // applied phase (ns, mod PERIOD)
    real t_g;             // current group start = clk_q posedge (ns)
    real t_qr;            // next clk_q posedge (ns)
    real t_ph[0:7];       // next clk_ph[k] posedge (ns)
    real d_step;          // last applied phase delta (wrap renumbering)
    real tr_rise;         // structural mode: ring edge time being anchored
    real d_clk;           // clk_q wake delay guard
    real t_g_prev = -1.0e9; // previous group start (wrap renumbering; <0 disables the first adjustment)
    int  gen = 0;         // schedule generation (publish counter)
    int  pub_seen_q = 0;  // per-consumer published counts

    // Apply the current pi_code/pi_frac to the applied phase.  The 11-bit
    // loop phase word {pi_code, pi_frac} is the phase directly; the delta is
    // wrapped mod PERIOD (single window steps are <= ~160 LSBs ~ 0.16 ns).
    task automatic apply_pi;
        real ph_new;
        begin
            ph_new  = ph_ns(pi_code * 256.0 + pi_frac);
            d_step  = wrap_d(ph_new - ph_cur);
            ph_cur  = ph_new;
        end
    endtask

    // Publish the next group's edge times: only clk_q needs scheduling (its
    // own posedge time).  The 8 sampling phases are generated relative to
    // that posedge (fixed PH_OFF + PERIOD/8*k offsets), so a phase move that
    // lands on a clk_q edge shifts the whole group -- pulses included --
    // exactly as a real re-timed PI clock would.
    task automatic publish_times;
        begin
            t_qr = t_g;
            pub_seen_q = gen + 1;
            gen = gen + 1;
        end
    endtask

`ifdef RX_CLK8_BEHAVIORAL
    // Behavioral scheduler: self-timed group cadence, phase from the loop.
    initial begin
        ph_cur = 0.0;
        t_g    = START_T_NS;
        publish_times;
        forever begin
            #(t_g - $realtime);   // arrive at the group start
            #0.001;               // let the loop's NBA update land
            apply_pi;
            t_g = t_g + PERIOD_NS + d_step;
            publish_times;
        end
    end
`else
    // Structural scheduler: the ring PACES the groups (one clean DCO_OUT
    // posedge = one group) and the group start advances delta-wise,
    // t_g += PERIOD + d, exactly like the behavioral path.  The ring edge
    // only marks the group; the PI word is applied at the clk_q boundary
    // (below), the same point behavioral's #(t_g-$realtime) arrival applies
    // it.  Sampling apply_pi on the ring edge read r_q a sliding ~+-1-group
    // offset from the window boundary the vote was computed on -- the vote
    // was then attributed to the wrong phase and the integrator railed to
    // FMAX (oracle-derived root cause of the structural ppm failures).
    reg dco_prev = 1'b0;
    always @(dco_out) begin
        if (dco_out === 1'b1 && dco_prev === 1'b0) begin
            tr_rise = $realtime;
            if (t_g_prev < 0.0) begin
                #0.001;               // settle
                apply_pi;
                t_g = tr_rise + ph_cur;        // first group: real anchor
            end
            t_g_prev = t_g;
            publish_times;
        end
        dco_prev <= dco_out;
    end
`endif

    // clk_q: posedge at each published t_qr, 50% duty.  Single waiter on its
    // own generation count (iverilog: one waiter per variable).
    int loc_seen_q = 0;
    initial begin
        clk_q = 1'b0;
        forever begin
            wait (pub_seen_q !== loc_seen_q);
            loc_seen_q = pub_seen_q;
            d_clk = t_qr - $realtime;
            // iverilog treats a negative # delay as "never resume"; the wake
            // can be a fraction of an LSB early on the first group.
            if (d_clk < 0.000001) d_clk = 0.000001;
            #(d_clk);
            clk_q = 1'b1;
`ifdef RX_CLK8_STRUCT
            // Apply the PI phase at the group boundary (same point
            // behavioral's #(t_g-$realtime) arrival applies it): read r_q
            // after the NBA settle on this clk_q edge, then schedule the
            // next group start delta-wise.  Applying on the ring edge
            // instead read the phase word a sliding offset from the window
            // boundary -> the vote was attributed to the wrong phase and
            // the integrator railed to FMAX.
            #0.001;
            apply_pi;
            t_g = t_qr + PERIOD_NS + d_step;
            t_g_prev = t_g;
            publish_times;
`endif
            #(PERIOD_NS / 2.0);
            clk_q = 1'b0;
        end
    end

    // clk_ph[k]: re-timed sampling phases.  Each phase rises a fixed offset
    // after the group start (the clk_q posedge): data taps (even k) at
    // PH_OFF + k*PERIOD/8, edge taps (odd k) half a UI later.  All offsets
    // are < PERIOD, so every pulse lands inside its own group regardless of
    // how the phase moved the group boundaries.
    genvar gp;
    generate
        for (gp = 0; gp < 8; gp = gp + 1) begin : g_phgen
            always @(posedge clk_q) begin
                #(PH_OFF_NS + (PERIOD_NS / 8.0) * gp);
                clk_ph[gp] = 1'b1;
                #(PERIOD_NS / 8.0);
                clk_ph[gp] = 1'b0;
            end
        end
    endgenerate

endmodule

`default_nettype wire
