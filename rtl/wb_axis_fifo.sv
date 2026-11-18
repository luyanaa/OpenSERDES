`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Wishbone B4 slave <-> AXI4-Stream data port.
//
// The SoC streaming wrapper's data plane is AXI4-Stream (`s_axis_*` /
// `m_axis_*`).  A Wishbone SoC has no AXIS port, so this is the data-plane
// adapter: a Wishbone slave whose data register is the stream.
//
//   write 0x0 : push one word into the TX stream
//   read  0x0 : pop one word from the RX stream
//   read  0x4 : status
//   write 0x8 : [0] tx_flush, drop every queued TX word
//
// Status word
// -----------
//   [7:0]   tx_level      words queued for transmission
//   [15:8]  rx_level      words readable
//   [16]    tx_full
//   [17]    rx_empty
//   [20:18] tx_underflow  saturating count of reads that found nothing
//   [21]    tx_flush      sticky, seen since `clear`
//
// Timing
// ------
// The module is a three-state per-access FSM, not a combinational ack:
//
//   IDLE   latch the request (address, we, data), decide `bad_req`
//   ACCESS the transfer happens here: one push, one pop, or a flush
//   RESP   raise exactly one of wb_ack / wb_err
//
// That ordering matters: `wb_dat_r` must read the head of the RX FIFO in the
// same cycle the master samples it, and `rx_rd_q` must not have advanced yet.
// Doing the pop in ACCESS and the response in RESP gives one full cycle of
// separation, so the read data is the word that was popped, not the next one.
//
// A Wishbone access is single-beat, so one stream transfer is one word per
// access; the FIFOs absorb the rate difference with the SerDes word clock.
// Writing into a full TX FIFO, or reading an empty RX FIFO, is reported with
// `wb_err` and is *not* silently dropped or duplicated: a bus that cannot see
// the difference between "accepted" and "lost" cannot carry a protocol.
//
// The FIFOs are synchronous (single clock).  Crossing into the SerDes word clock
// needs an `async_fifo` per direction, which is what the wrapper already uses;
// this module deliberately stays single-clock so it can sit on either side.
// -----------------------------------------------------------------------------
module wb_axis_fifo #(
    parameter int DATA_WIDTH = 32,
    parameter int FIFO_DEPTH = 16,
    parameter int ADDR_WIDTH = 16
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  clear,
    // Wishbone B4 slave.
    input  logic                  wb_cyc,
    input  logic                  wb_stb,
    input  logic [ADDR_WIDTH-1:0] wb_adr,
    input  logic                  wb_we,
    input  logic [DATA_WIDTH-1:0] wb_dat_w,
    input  logic [DATA_WIDTH/8-1:0] wb_sel,
    output logic                  wb_ack,
    output logic                  wb_err,
    output logic [DATA_WIDTH-1:0] wb_dat_r,
    // TX stream: words written through Wishbone are emitted here.
    output logic [DATA_WIDTH-1:0] m_axis_tdata,
    output logic                  m_axis_tvalid,
    input  logic                  m_axis_tready,
    // RX stream: words presented here are readable through Wishbone.
    input  logic [DATA_WIDTH-1:0] s_axis_tdata,
    input  logic                  s_axis_tvalid,
    output logic                  s_axis_tready
);
    localparam int AW = (FIFO_DEPTH <= 1) ? 1 : $clog2(FIFO_DEPTH);
    localparam int DW = AW + 1;

    typedef enum logic [1:0] { W_IDLE, W_ACCESS, W_RESP } state_t;
    state_t state_q;

    // ---- latched request ---------------------------------------------------
    logic [ADDR_WIDTH-1:0] adr_q;
    logic                  we_q;
    logic [DATA_WIDTH-1:0] dat_q;
    logic                  sel_ok_q;
    logic                  addr_ok_q;

    // Address decode on the latched address.
    logic [ADDR_WIDTH-1:0] word_addr;
    logic                  is_data, is_status, is_ctrl;

    // Word-aligned only: the low address bits must be zero, and they are part
    // of the address check rather than silently dropped.
    assign word_addr = {adr_q[ADDR_WIDTH-1:2], 2'b00};
    assign is_data   = (word_addr == '0);
    assign is_status = (word_addr == ADDR_WIDTH'(4));
    assign is_ctrl   = (word_addr == ADDR_WIDTH'(8));
    assign addr_ok_q = (is_data || is_status || is_ctrl) &&
                       (adr_q[1:0] == 2'b00);

    // ---- FIFOs -------------------------------------------------------------
    logic [DATA_WIDTH-1:0] tx_mem [FIFO_DEPTH];
    logic [AW-1:0]         tx_wr_q, tx_rd_q;
    logic [DW-1:0]         tx_used_q;

    logic [DATA_WIDTH-1:0] rx_mem [FIFO_DEPTH];
    logic [AW-1:0]         rx_wr_q, rx_rd_q;
    logic [DW-1:0]         rx_used_q;

    logic tx_full, rx_empty;
    assign tx_full  = (tx_used_q == DW'(FIFO_DEPTH));
    assign rx_empty = (rx_used_q == '0);

    // ---- one transfer per access, in W_ACCESS ------------------------------
    logic do_push, do_pop, do_flush, bad_req;
    assign do_push  = (state_q == W_ACCESS) &&  we_q && is_data && !tx_full;
    // The pop happens in the *response* cycle: `wb_dat_r` reads the head
    // combinationally, so the pointer must still be pointing at the word the
    // master is sampling.
    assign do_pop   = (state_q == W_RESP) && !we_q && is_data && !rx_empty;
    assign do_flush = (state_q == W_ACCESS) && we_q && is_ctrl && dat_q[0];
    // A request is rejected if the address is unknown, the select is partial
    // (this register file has no byte enables), or the FIFO cannot serve it.
    assign bad_req  = !sel_ok_q || !addr_ok_q ||
                      ((state_q == W_ACCESS) && we_q && is_data && tx_full) ||
                      ((state_q == W_ACCESS) && !we_q && is_data && rx_empty);

    // ---- response ----------------------------------------------------------
    assign wb_ack   = (state_q == W_RESP) && !err_q;
    assign wb_err   = (state_q == W_RESP) &&  err_q;
    assign wb_dat_r = is_status ? {10'b0, tx_flush_q, 3'(underflow_q),
                                   rx_empty, tx_full,
                                   8'(rx_used_q), 8'(tx_used_q)}
                              : rx_head;

    // Read data is the RX FIFO head, sampled combinationally in the response
    // cycle.  The pop happens on that same edge, after the master has the word.
    logic [DATA_WIDTH-1:0] rx_head;
    assign rx_head = rx_mem[rx_rd_q];

    // ---- flush / sticky telemetry ------------------------------------------
    logic        tx_flush_q;
    logic [2:0]  underflow_q;

    // ---- TX stream ---------------------------------------------------------
    assign m_axis_tvalid = (tx_used_q != '0);
    assign m_axis_tdata  = tx_mem[tx_rd_q];
    logic  tx_consume;
    assign tx_consume = m_axis_tvalid && m_axis_tready;

    // ---- RX stream ---------------------------------------------------------
    logic rx_accept;
    assign s_axis_tready = (rx_used_q != DW'(FIFO_DEPTH));
    assign rx_accept     = s_axis_tvalid && s_axis_tready;

    // ---- FSM ---------------------------------------------------------------
    logic err_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_q     <= W_IDLE;
            adr_q       <= '0;
            we_q        <= 1'b0;
            dat_q       <= '0;
            sel_ok_q    <= 1'b0;
            err_q       <= 1'b0;
            tx_wr_q     <= '0;
            tx_rd_q     <= '0;
            tx_used_q   <= '0;
            rx_wr_q     <= '0;
            rx_rd_q     <= '0;
            rx_used_q   <= '0;
            tx_flush_q  <= 1'b0;
            underflow_q <= '0;
        end else if (clear) begin
            state_q     <= W_IDLE;
            tx_wr_q     <= '0;
            tx_rd_q     <= '0;
            tx_used_q   <= '0;
            rx_wr_q     <= '0;
            rx_rd_q     <= '0;
            rx_used_q   <= '0;
            tx_flush_q  <= 1'b0;
            underflow_q <= '0;
        end else begin
            // Stream movement is independent of the bus FSM, except that a
            // pop/push is gated by W_ACCESS above.
            if (do_flush) begin
                tx_wr_q    <= '0;
                tx_rd_q    <= '0;
                tx_used_q  <= '0;
                tx_flush_q <= 1'b1;
            end else begin
                if (do_push) begin
                    tx_mem[tx_wr_q] <= dat_q;
                    tx_wr_q <= tx_wr_q + 1'b1;
                end
                if (tx_consume)
                    tx_rd_q <= tx_rd_q + 1'b1;
                if (do_push && !tx_consume)
                    tx_used_q <= tx_used_q + 1'b1;
                else if (!do_push && tx_consume)
                    tx_used_q <= tx_used_q - 1'b1;
            end

            if (rx_accept) begin
                rx_mem[rx_wr_q] <= s_axis_tdata;
                rx_wr_q <= rx_wr_q + 1'b1;
            end
            if (do_pop)
                rx_rd_q <= rx_rd_q + 1'b1;
            if (rx_accept && !do_pop)
                rx_used_q <= rx_used_q + 1'b1;
            else if (!rx_accept && do_pop)
                rx_used_q <= rx_used_q - 1'b1;

            // An empty read is reported through wb_err above; count it so a
            // master polling an empty port is visible in the status word.
            if ((state_q == W_RESP) && !we_q && is_data && rx_empty &&
                underflow_q != 3'h7)
                underflow_q <= underflow_q + 1'b1;

            // Bus FSM.
            case (state_q)
                W_IDLE: begin
                    if (wb_cyc && wb_stb) begin
                        adr_q    <= wb_adr;
                        we_q     <= wb_we;
                        dat_q    <= wb_dat_w;
                        sel_ok_q <= (&wb_sel);
                        state_q  <= W_ACCESS;
                    end
                end
                W_ACCESS: begin
                    err_q   <= bad_req;
                    state_q <= W_RESP;
                end
                W_RESP: begin
                    state_q <= W_IDLE;
                end
                default: state_q <= W_IDLE;
            endcase
        end
    end

endmodule
