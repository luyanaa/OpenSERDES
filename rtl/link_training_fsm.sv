`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Generic link-training FSM.
//
// The FSM scans a phase axis and then a threshold axis, selecting the candidate
// with the fewest errors accumulated during a fixed dwell window.  It does not
// contain a phase interpolator or a threshold DAC: `phase_sel`/`thresh_sel`
// are actuator requests, and `phase_we`/`thresh_we` are the corresponding
// one-cycle load strobes.  A generator must only connect an axis when the PDK
// backend actually exposes that actuator.
//
// `error_count` is a free-running, monotonically increasing error counter
// (for example, the PRBS checker error count).  The FSM subtracts the value
// captured at the start of each candidate, so the counter does not need to be
// cleared between candidates.
//
// States:
//   IDLE        - waiting for `start`
//   SCAN_PHASE  - sweep phase_sel, threshold held at 0
//   SCAN_THRESH - sweep thresh_sel at the best phase
//   LOCKED      - freeze the best settings and monitor error_count
//
// A `start` pulse while LOCKED requests a retrain.  In LOCKED, a sustained
// error-rate increase also causes a retrain.  `retrain` is a one-cycle pulse.
// -----------------------------------------------------------------------------
module link_training_fsm #(
    parameter int PHASE_STEPS        = 8,
    parameter int THRESH_STEPS       = 8,
    parameter int DWELL_CYCLES       = 64,
    parameter int ERROR_WIDTH        = 16,
    parameter int RETRAIN_ERR_THRESH = 4,
    parameter int RETRAIN_WINDOW     = 256
) (
    input  logic                        clk,
    input  logic                        rst_n,
    input  logic                        enable,
    input  logic                        start,
    input  logic [ERROR_WIDTH-1:0]      error_count,
    output logic [((PHASE_STEPS > 1) ? $clog2(PHASE_STEPS) : 1)-1:0] phase_sel,
    output logic [((THRESH_STEPS > 1) ? $clog2(THRESH_STEPS) : 1)-1:0] thresh_sel,
    output logic                        phase_we,
    output logic                        thresh_we,
    output logic                        busy,
    output logic                        locked,
    output logic                        retrain,
    output logic [2:0]                  state,
    output logic [((PHASE_STEPS > 1) ? $clog2(PHASE_STEPS) : 1)-1:0] best_phase,
    output logic [((THRESH_STEPS > 1) ? $clog2(THRESH_STEPS) : 1)-1:0] best_thresh,
    output logic [ERROR_WIDTH-1:0]      best_errors
);

    localparam int PW = (PHASE_STEPS > 1) ? $clog2(PHASE_STEPS) : 1;
    localparam int TW = (THRESH_STEPS > 1) ? $clog2(THRESH_STEPS) : 1;
    localparam int EW = (ERROR_WIDTH  > 1) ? ERROR_WIDTH : 1;

    localparam logic [2:0] S_IDLE        = 3'd0;
    localparam logic [2:0] S_SCAN_PHASE  = 3'd1;
    localparam logic [2:0] S_SCAN_THRESH = 3'd2;
    localparam logic [2:0] S_LOCKED      = 3'd3;

    logic [2:0]        state_q;
    logic [PW-1:0]     phase_q;
    logic [TW-1:0]     thresh_q;
    logic [PW-1:0]     best_phase_q;
    logic [TW-1:0]     best_thresh_q;
    logic [EW-1:0]     best_errors_q;
    logic [EW-1:0]     baseline_q;
    logic [EW-1:0]     locked_baseline_q;
    logic [31:0]       dwell_q;
    logic [31:0]       monitor_q;
    logic              phase_we_q;
    logic              thresh_we_q;
    logic              retrain_q;
    logic              locked_q;

    logic [PW-1:0] cand_phase;
    logic [TW-1:0] cand_thresh;
    logic [EW-1:0] cand_errors;
    logic [EW-1:0] delta;
    logic [EW-1:0] monitor_delta;

    assign phase_sel   = phase_q;
    assign thresh_sel  = thresh_q;
    assign phase_we    = phase_we_q;
    assign thresh_we   = thresh_we_q;
    assign busy        = (state_q != S_IDLE) && (state_q != S_LOCKED);
    assign locked      = locked_q;
    assign retrain     = retrain_q;
    assign state       = state_q;
    assign best_phase  = best_phase_q;
    assign best_thresh = best_thresh_q;
    assign best_errors = best_errors_q;

    // Saturating subtraction keeps a wrapped / reset error counter from
    // looking like a negative error count.
    assign delta = (error_count >= baseline_q) ? (error_count - baseline_q)
                                                : {EW{1'b1}};
    assign monitor_delta = (error_count >= locked_baseline_q)
                           ? (error_count - locked_baseline_q)
                           : {EW{1'b1}};

    assign cand_phase = (delta < best_errors_q) ? phase_q : best_phase_q;
    assign cand_thresh = (delta < best_errors_q) ? thresh_q : best_thresh_q;
    assign cand_errors = (delta < best_errors_q) ? delta : best_errors_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_q          <= S_IDLE;
            phase_q          <= '0;
            thresh_q         <= '0;
            best_phase_q     <= '0;
            best_thresh_q    <= '0;
            best_errors_q    <= '1;
            baseline_q       <= '0;
            locked_baseline_q <= '0;
            dwell_q          <= '0;
            monitor_q        <= '0;
            phase_we_q       <= 1'b0;
            thresh_we_q      <= 1'b0;
            retrain_q        <= 1'b0;
            locked_q         <= 1'b0;
        end else begin
            phase_we_q  <= 1'b0;
            thresh_we_q <= 1'b0;
            retrain_q   <= 1'b0;

            if (!enable) begin
                state_q   <= S_IDLE;
                locked_q  <= 1'b0;
                dwell_q   <= '0;
                monitor_q <= '0;
            end else begin
                case (state_q)
                    S_IDLE: begin
                        if (start) begin
                            phase_q        <= '0;
                            thresh_q       <= '0;
                            best_phase_q   <= '0;
                            best_thresh_q  <= '0;
                            best_errors_q  <= '1;
                            baseline_q     <= error_count;
                            locked_q       <= 1'b0;
                            dwell_q        <= '0;
                            state_q        <= S_SCAN_PHASE;
                            phase_we_q     <= 1'b1;
                            thresh_we_q    <= 1'b1;
                        end
                    end

                    S_SCAN_PHASE: begin
                        if (dwell_q >= DWELL_CYCLES - 1) begin
                            best_phase_q  <= cand_phase;
                            best_errors_q <= cand_errors;
                            dwell_q       <= '0;
                            baseline_q    <= error_count;
                            if (phase_q == PW'(PHASE_STEPS - 1)) begin
                                phase_q      <= cand_phase;
                                phase_we_q   <= 1'b1;
                                best_errors_q <= '1;
                                thresh_q     <= '0;
                                thresh_we_q  <= 1'b1;
                                state_q      <= S_SCAN_THRESH;
                            end else begin
                                phase_q    <= phase_q + 1'b1;
                                phase_we_q <= 1'b1;
                            end
                        end else begin
                            dwell_q <= dwell_q + 1'b1;
                        end
                    end

                    S_SCAN_THRESH: begin
                        if (dwell_q >= DWELL_CYCLES - 1) begin
                            best_thresh_q <= cand_thresh;
                            best_errors_q <= cand_errors;
                            dwell_q       <= '0;
                            baseline_q    <= error_count;
                            if (thresh_q == TW'(THRESH_STEPS - 1)) begin
                                thresh_q         <= cand_thresh;
                                thresh_we_q      <= 1'b1;
                                locked_q         <= 1'b1;
                                state_q          <= S_LOCKED;
                                monitor_q        <= '0;
                                locked_baseline_q <= error_count;
                            end else begin
                                thresh_q    <= thresh_q + 1'b1;
                                thresh_we_q <= 1'b1;
                            end
                        end else begin
                            dwell_q <= dwell_q + 1'b1;
                        end
                    end

                    S_LOCKED: begin
                        if (start) begin
                            retrain_q <= 1'b1;
                            locked_q  <= 1'b0;
                            state_q   <= S_IDLE;
                        end else if (monitor_q >= RETRAIN_WINDOW - 1) begin
                            monitor_q <= '0;
                            locked_baseline_q <= error_count;
                            if (monitor_delta > EW'(RETRAIN_ERR_THRESH)) begin
                                // Retrain autonomously: do not require a second
                                // external start pulse after a monitor failure.
                                retrain_q     <= 1'b1;
                                locked_q      <= 1'b0;
                                phase_q       <= '0;
                                thresh_q      <= '0;
                                best_phase_q  <= '0;
                                best_thresh_q <= '0;
                                best_errors_q <= '1;
                                baseline_q    <= error_count;
                                dwell_q       <= '0;
                                phase_we_q    <= 1'b1;
                                thresh_we_q   <= 1'b1;
                                state_q       <= S_SCAN_PHASE;
                            end
                        end else begin
                            monitor_q <= monitor_q + 1'b1;
                        end
                    end

                    default: state_q <= S_IDLE;
                endcase
            end
        end
    end

endmodule
