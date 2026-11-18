`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Link-up watchdog.
//
// Every "wait until the link comes up" state machine in this project had the
// same hole: `link_training_fsm` scans, `lane_deskew` hunts, `pcs_8b10b_comma`
// searches for a comma, `axis_frame_depacketizer` waits for the rest of a
// fragment chain -- and none of them can distinguish "still trying" from
// "structurally stuck".  A dead channel and a slow one look identical, and the
// only symptom is silence.
//
// Reference: LiteICLink restarts the whole transceiver init sequence when it
// does not complete within 1 ms (7-Series), 10 ms (UltraScale) or 4 ms (GTP RX).
// This is the digital equivalent: a caller says "I expect progress within
// TIMEOUT_CYCLES", arms the watchdog when it starts waiting, and gets a
// `timeout` pulse plus a sticky `stuck` flag if nothing happens.
//
// Usage:
//   - `restart` (pulse) begins a new window (call it when entering a wait state)
//   - `progress` (pulse) proves the wait is advancing
//   - `timeout` pulses once per expired window, and the window restarts
//   - `stuck` latches until `clear`, so a recovered link still reports that it
//     had to give up at least once
// -----------------------------------------------------------------------------
module link_watchdog #(
    // Cycles without progress before the wait is declared stuck.  A power of
    // two is not required, but 0 disables the watchdog entirely.
    parameter int TIMEOUT_CYCLES = 65536
) (
    input  logic clk,
    input  logic rst_n,
    input  logic enable,
    input  logic clear,
    input  logic restart,       // pulse: begin a new window
    input  logic progress,      // pulse: the wait is advancing
    output logic timeout,       // pulse: this window expired
    output logic stuck,         // sticky since the last clear
    output logic [15:0] timeout_count
);
    localparam int CW = (TIMEOUT_CYCLES <= 1) ? 1 : $clog2(TIMEOUT_CYCLES + 1);

    logic [CW-1:0] count_q;
    logic          running_q;
    logic          stuck_q;
    logic [15:0]   count_events_q;

    assign timeout       = running_q && (count_q == CW'(TIMEOUT_CYCLES));
    assign stuck         = stuck_q;
    assign timeout_count = count_events_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count_q        <= '0;
            running_q      <= 1'b0;
            stuck_q        <= 1'b0;
            count_events_q <= '0;
        end else if (clear) begin
            count_q        <= '0;
            running_q      <= 1'b0;
            stuck_q        <= 1'b0;
            count_events_q <= '0;
        end else if (TIMEOUT_CYCLES > 0) begin
            if (!enable) begin
                // A disabled link is not a stuck link.
                count_q   <= '0;
                running_q <= 1'b0;
            end else if (restart) begin
                count_q   <= '0;
                running_q <= 1'b1;
            end else if (progress) begin
                count_q <= '0;
            end else if (running_q) begin
                if (count_q == CW'(TIMEOUT_CYCLES)) begin
                    // Expired: pulse, count it, and give the caller a fresh
                    // window so a retry loop is not silently abandoned.
                    count_q        <= '0;
                    stuck_q        <= 1'b1;
                    count_events_q <= (count_events_q == 16'hFFFF)
                                      ? count_events_q
                                      : count_events_q + 1'b1;
                end else begin
                    count_q <= count_q + 1'b1;
                end
            end
        end
    end

endmodule
