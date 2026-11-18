`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Wishbone B4 slave -> APB master bridge.
//
// The SoC streaming wrapper exposes an APB control plane
// (`rtl/apb_csr.sv`, 20 CSRs).  A Wishbone SoC cannot talk to it directly, and
// duplicating the whole register file behind a second interface would be two
// implementations of one contract that will drift.  This bridge is the adapter
// instead: the APB side of the wrapper stays the single source of truth for the
// CSR map, and a Wishbone master reaches it through a standard B4 slave port.
//
// Why not APB -> Wishbone: the wrapper is the thing that exists, and a
// Wishbone-to-APB shim is strictly smaller than a second CSR file.
//
// Protocol
// --------
// Wishbone B4 (classic, non-pipelined), 32-bit, word aligned:
//   - `wb_adr` is a **byte** address; `paddr` is passed through unchanged, and
//     `apb_csr` already rejects non-word-aligned accesses (`pslverr`);
//   - `wb_ack` is one cycle, after the APB access completes;
//   - `wb_err` is `pslverr` from the APB side, so an out-of-range or
//     misaligned access is reported to the Wishbone master and not silently
//     swallowed;
//   - `wb_sel` must be all-ones (32-bit data); a partial select is reported as
//     an error rather than being ignored, because this register file has no
//     byte-enable semantics.
//
// The bridge is a two-state FSM per access:
//   IDLE  -> latches the Wishbone request, raises psel (APB setup phase)
//   ACCESS-> raises penable, waits for pready, then wb_ack/wb_err
// APB is a sampled interface with pready high whenever psel is high, so the
// access completes in the cycle after setup.
// -----------------------------------------------------------------------------
module wb_apb_bridge #(
    parameter int ADDR_WIDTH = 16,
    parameter int DATA_WIDTH = 32
) (
    input  logic                    clk,
    input  logic                    rst_n,
    // Wishbone B4 slave.
    input  logic                    wb_cyc,
    input  logic                    wb_stb,
    input  logic [ADDR_WIDTH-1:0]   wb_adr,
    input  logic                    wb_we,
    input  logic [DATA_WIDTH-1:0]   wb_dat_w,
    input  logic [DATA_WIDTH/8-1:0] wb_sel,
    output logic                    wb_ack,
    output logic                    wb_err,
    output logic [DATA_WIDTH-1:0]   wb_dat_r,
    // APB master.
    output logic                    psel,
    output logic                    penable,
    output logic [ADDR_WIDTH-1:0]   paddr,
    output logic                    pwrite,
    output logic [DATA_WIDTH-1:0]   pwdata,
    input  logic                    pready,
    input  logic [DATA_WIDTH-1:0]   prdata,
    input  logic                    pslverr
);
    typedef enum logic [1:0] { W_IDLE, W_SETUP, W_ACCESS, W_DONE } state_t;
    state_t state_q;

    logic                  sel_ok;
    logic [ADDR_WIDTH-1:0] adr_q;
    logic                  we_q;
    logic [DATA_WIDTH-1:0] dat_q;

    // Errors detected by the bridge itself (before APB is even reached), and
    // the APB verdict captured on the ACCESS cycle: `wb_err` and `wb_ack` are
    // both driven in W_DONE, so the master sees one cycle with exactly one of
    // them high.
    logic                  local_err_q;
    logic                  apb_err_q;

    // A 32-bit register file has no byte-enable semantics: accept only a full
    // select, and say so instead of silently reading/writing the whole word.
    assign sel_ok = &wb_sel;

    assign paddr   = adr_q;
    assign pwrite  = we_q;
    assign pwdata  = dat_q;

    wire any_err = local_err_q || apb_err_q;

    assign wb_ack   = (state_q == W_DONE) && !any_err;
    assign wb_err   = (state_q == W_DONE) &&  any_err;
    assign wb_dat_r = prdata;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_q     <= W_IDLE;
            adr_q       <= '0;
            we_q        <= 1'b0;
            dat_q       <= '0;
            local_err_q <= 1'b0;
            apb_err_q   <= 1'b0;
        end else begin
            case (state_q)
                W_IDLE: begin
                    if (wb_cyc && wb_stb) begin
                        adr_q       <= wb_adr;
                        we_q        <= wb_we;
                        dat_q       <= wb_dat_w;
                        // Bridge-level checks, made now so they cannot be
                        // confused with the APB verdict.
                        local_err_q <= !sel_ok || (wb_adr[1:0] != 2'b00);
                        state_q     <= W_SETUP;
                    end
                end
                W_SETUP: begin
                    // APB setup phase: psel high, penable low for one cycle.
                    // A request the bridge already rejected is answered from
                    // W_DONE without touching the register file: `apb_csr` has
                    // no byte-enable semantics, so forwarding a partial write
                    // would commit the whole word.
                    if (local_err_q)
                        state_q <= W_DONE;
                    else
                        state_q <= W_ACCESS;
                end
                W_ACCESS: begin
                    if (pready) begin
                        apb_err_q <= pslverr;
                        state_q   <= W_DONE;
                    end
                end
                W_DONE: begin
                    // One-cycle ack; a new request is taken on the next cycle.
                    state_q <= W_IDLE;
                end
                default: state_q <= W_IDLE;
            endcase
        end
    end

    assign psel    = !local_err_q &&
                     ((state_q == W_SETUP) || (state_q == W_ACCESS));
    assign penable = !local_err_q && (state_q == W_ACCESS);

endmodule
