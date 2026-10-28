`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Multi-clock SoC wrapper: APB CSR + AXIS async FIFOs + source-synchronous link.
//
// Domains:
//   pclk        : APB CSR and synchronized status
//   s_axis_aclk : TX AXIS input, TX FIFO write side
//   tx_clk      : TX FIFO read / serializer
//   rx_clk      : RX core / RX FIFO write side
//   m_axis_aclk : RX AXIS output, RX FIFO read side
//
// TX AXIS frame = WORDS+1 beats: comma (0xBC) + WORDS payload words.
// RX AXIS frame = WORDS payload words.
//
// Control/status are static levels or pulses synchronized with cdc_sync.  The
// multi-bit status bus is synchronized bit-by-bit for observability; software
// must treat it as a set of independent status bits, not a coherent snapshot.
// -----------------------------------------------------------------------------
module serdes_soc_cdc_wrapper #(
    parameter int DATA_WIDTH  = 8,
    parameter int WORDS       = 4,
    parameter bit LSB_FIRST   = 1'b1,
    parameter int SYNC_LEN    = 8,
    parameter logic [SYNC_LEN-1:0] SYNC_PATTERN = 'hBC,
    parameter int ADAPT_FRAC_BITS = 12,
    parameter int FIFO_DEPTH  = 16
) (
    // APB
    input  logic                              pclk,
    input  logic                              presetn,
    input  logic [15:0]                       paddr,
    input  logic                              psel,
    input  logic                              penable,
    input  logic                              pwrite,
    input  logic [31:0]                       pwdata,
    output logic                              pready,
    output logic [31:0]                       prdata,
    output logic                              pslverr,

    // TX AXIS
    input  logic                              s_axis_aclk,
    input  logic                              s_axis_aresetn,
    input  logic                              s_axis_tvalid,
    output logic                              s_axis_tready,
    input  logic [DATA_WIDTH-1:0]             s_axis_tdata,
    input  logic                              s_axis_tlast,

    // RX AXIS
    input  logic                              m_axis_aclk,
    input  logic                              m_axis_aresetn,
    output logic                              m_axis_tvalid,
    input  logic                              m_axis_tready,
    output logic [DATA_WIDTH-1:0]             m_axis_tdata,
    output logic                              m_axis_tlast,

    // Link
    input  logic                              tx_clk,
    input  logic                              tx_rst_n,
    output logic                              tx_serial_out,
    output logic                              tx_serial_valid,
    input  logic                              rx_clk,
    input  logic                              rx_rst_n,
    input  logic                              rx_serial_in,

    // Observability
    output logic                              rx_sample_strobe,
    output logic                              rx_serial_data,
    output logic                              rx_serial_valid,
    output logic signed [ADAPT_FRAC_BITS:0]   rx_freq_trim,
    output logic                              rx_clk_out,
    output logic [7:0][31:0]                  csr_regs
);

    localparam int TX_WORDS = WORDS + 1;
    localparam int RX_WORDS = WORDS;

    // -------------------------------------------------------------------------
    // APB control / status
    // -------------------------------------------------------------------------
    logic [31:0] status;

    logic tx_enable_p, rx_enable_p, rx_flush_p, tx_clear_p, rx_clear_p;
    logic tx_enable_tx, tx_clear_tx, tx_clear_tx_d, tx_clear_pulse;
    logic rx_enable_rx, rx_flush_rx, rx_flush_rx_d, rx_flush_pulse;
    logic rx_clear_rx, rx_clear_rx_d, rx_clear_pulse;
    logic tx_axis_ready_p, m_axis_valid_p;
    logic [2:0] tx_status_tx, tx_status_p;
    logic [5:0] rx_status_rx, rx_status_p;

    assign tx_enable_p = csr_regs[0][0];
    assign rx_enable_p = csr_regs[0][1];
    assign rx_flush_p  = csr_regs[0][2];
    assign tx_clear_p  = csr_regs[0][3];
    assign rx_clear_p  = csr_regs[0][4];

    assign status = {21'b0, m_axis_valid_p, rx_status_p, tx_status_p, tx_axis_ready_p};

    apb_csr #(
        .NUM_REGS(8),
        .DATA_WIDTH(32),
        .ADDR_WIDTH(16)
    ) u_csr (
        .pclk(pclk),
        .presetn(presetn),
        .paddr(paddr),
        .psel(psel),
        .penable(penable),
        .pwrite(pwrite),
        .pwdata(pwdata),
        .pready(pready),
        .prdata(prdata),
        .pslverr(pslverr),
        .regs_o(csr_regs),
        .status_i(status)
    );

    // Control into TX domain.
    cdc_sync #(.WIDTH(1)) u_tx_enable (
        .clk(tx_clk), .rst_n(tx_rst_n), .d(tx_enable_p), .q(tx_enable_tx)
    );
    cdc_sync #(.WIDTH(1)) u_tx_clear (
        .clk(tx_clk), .rst_n(tx_rst_n), .d(tx_clear_p), .q(tx_clear_tx)
    );
    always_ff @(posedge tx_clk or negedge tx_rst_n) begin
        if (!tx_rst_n)
            tx_clear_tx_d <= 1'b0;
        else
            tx_clear_tx_d <= tx_clear_tx;
    end
    assign tx_clear_pulse = tx_clear_tx & ~tx_clear_tx_d;

    // Control into RX domain.
    cdc_sync #(.WIDTH(1)) u_rx_enable (
        .clk(rx_clk), .rst_n(rx_rst_n), .d(rx_enable_p), .q(rx_enable_rx)
    );
    cdc_sync #(.WIDTH(1)) u_rx_flush (
        .clk(rx_clk), .rst_n(rx_rst_n), .d(rx_flush_p), .q(rx_flush_rx)
    );
    cdc_sync #(.WIDTH(1)) u_rx_clear (
        .clk(rx_clk), .rst_n(rx_rst_n), .d(rx_clear_p), .q(rx_clear_rx)
    );
    always_ff @(posedge rx_clk or negedge rx_rst_n) begin
        if (!rx_rst_n) begin
            rx_flush_rx_d  <= 1'b0;
            rx_clear_rx_d  <= 1'b0;
        end else begin
            rx_flush_rx_d  <= rx_flush_rx;
            rx_clear_rx_d  <= rx_clear_rx;
        end
    end
    assign rx_flush_pulse = rx_flush_rx & ~rx_flush_rx_d;
    assign rx_clear_pulse = rx_clear_rx & ~rx_clear_rx_d;

    // -------------------------------------------------------------------------
    // TX path: AXIS async FIFO -> adapter -> serializer_stream
    // -------------------------------------------------------------------------
    logic [DATA_WIDTH:0] tx_fifo_wdata, tx_fifo_rdata;
    logic tx_fifo_wfull, tx_fifo_rempty, tx_fifo_winc, tx_fifo_rinc;
    logic tx_fifo_valid, tx_fifo_ready;
    logic tx_in_valid, tx_in_ready;
    logic [DATA_WIDTH-1:0] tx_in_word;
    logic tx_busy, tx_frame_done, tx_frame_error;
    logic tx_done_sticky;

    assign s_axis_tready  = ~tx_fifo_wfull;
    assign tx_fifo_winc   = s_axis_tvalid && ~tx_fifo_wfull;
    assign tx_fifo_wdata  = {s_axis_tlast, s_axis_tdata};
    assign tx_fifo_valid   = ~tx_fifo_rempty;
    assign tx_fifo_rinc    = tx_fifo_valid && tx_fifo_ready;

    async_fifo #(
        .DATA_WIDTH(DATA_WIDTH + 1),
        .DEPTH(FIFO_DEPTH),
        .SYNC_STAGES(2)
    ) u_tx_fifo (
        .wclk(s_axis_aclk),
        .wrst_n(s_axis_aresetn),
        .winc(tx_fifo_winc),
        .wdata(tx_fifo_wdata),
        .wfull(tx_fifo_wfull),
        .rclk(tx_clk),
        .rrst_n(tx_rst_n),
        .rinc(tx_fifo_rinc),
        .rdata(tx_fifo_rdata),
        .rempty(tx_fifo_rempty)
    );

    axis_tx_adapter #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(TX_WORDS)
    ) u_tx_axis (
        .clk(tx_clk),
        .rst_n(tx_rst_n),
        .clear(tx_clear_pulse),
        .s_axis_tvalid(tx_fifo_valid),
        .s_axis_tready(tx_fifo_ready),
        .s_axis_tdata(tx_fifo_rdata[DATA_WIDTH-1:0]),
        .s_axis_tlast(tx_fifo_rdata[DATA_WIDTH]),
        .in_valid(tx_in_valid),
        .in_ready(tx_in_ready),
        .in_word(tx_in_word),
        .frame_error(tx_frame_error)
    );

    serializer_stream #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(TX_WORDS),
        .LSB_FIRST(LSB_FIRST)
    ) u_serializer (
        .clk(tx_clk),
        .rst_n(tx_rst_n),
        .enable(tx_enable_tx),
        .in_valid(tx_in_valid),
        .in_ready(tx_in_ready),
        .in_word(tx_in_word),
        .serial_out(tx_serial_out),
        .serial_valid(tx_serial_valid),
        .busy(tx_busy),
        .frame_done(tx_frame_done)
    );

    always_ff @(posedge tx_clk or negedge tx_rst_n) begin
        if (!tx_rst_n)
            tx_done_sticky <= 1'b0;
        else if (tx_clear_pulse)
            tx_done_sticky <= 1'b0;
        else if (tx_frame_done)
            tx_done_sticky <= 1'b1;
    end

    assign tx_status_tx = {tx_frame_error, tx_done_sticky, tx_busy};

    // -------------------------------------------------------------------------
    // RX path: RX core -> adapter -> AXIS async FIFO
    // -------------------------------------------------------------------------
    logic [RX_WORDS-1:0][DATA_WIDTH-1:0] rx_par_data;
    logic rx_frame_valid, rx_par_valid, rx_busy;
    logic rx_lock, rx_lock_stale, rx_frame_locked, rx_frame_overflow;
    logic rx_axis_tvalid, rx_axis_tready, rx_axis_tlast;
    logic [DATA_WIDTH-1:0] rx_axis_tdata;
    logic [DATA_WIDTH:0] rx_fifo_wdata, rx_fifo_rdata;
    logic rx_fifo_wfull, rx_fifo_rempty, rx_fifo_winc, rx_fifo_rinc;
    logic rx_frame_seen_sticky;

    serdes_rx_digital #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(RX_WORDS),
        .LSB_FIRST(LSB_FIRST),
        .OVERSAMPLE(1),
        .CDR_BYPASS(1'b1),
        .ALIGN_MODE(2),
        .SYNC_LEN(SYNC_LEN),
        .SYNC_PATTERN(SYNC_PATTERN),
        .ADAPT_FRAC_BITS(ADAPT_FRAC_BITS)
    ) u_rx (
        .rx_clk(rx_clk),
        .rx_rst_n(rx_rst_n),
        .rx_enable(rx_enable_rx),
        .rx_flush(rx_flush_pulse),
        .rx_align(1'b0),
        .rx_serial_in(rx_serial_in),
        .rx_par_data(rx_par_data),
        .rx_par_valid(rx_par_valid),
        .rx_busy(rx_busy),
        .rx_lock(rx_lock),
        .rx_lock_stale(rx_lock_stale),
        .rx_sample_strobe(rx_sample_strobe),
        .rx_clk_out(rx_clk_out),
        .rx_frame_locked(rx_frame_locked),
        .rx_frame_valid(rx_frame_valid),
        .rx_serial_data(rx_serial_data),
        .rx_serial_valid(rx_serial_valid),
        .rx_freq_trim(rx_freq_trim)
    );

    axis_rx_adapter #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(RX_WORDS)
    ) u_rx_axis (
        .clk(rx_clk),
        .rst_n(rx_rst_n),
        .clear(rx_clear_pulse | rx_flush_pulse),
        .par_valid(rx_frame_valid),
        .par_data(rx_par_data),
        .m_axis_tvalid(rx_axis_tvalid),
        .m_axis_tready(rx_axis_tready),
        .m_axis_tdata(rx_axis_tdata),
        .m_axis_tlast(rx_axis_tlast),
        .frame_overflow(rx_frame_overflow)
    );

    assign rx_fifo_winc  = rx_axis_tvalid && ~rx_fifo_wfull;
    assign rx_fifo_wdata = {rx_axis_tlast, rx_axis_tdata};
    assign rx_axis_tready = ~rx_fifo_wfull;
    assign rx_fifo_rinc   = m_axis_tvalid && m_axis_tready;

    async_fifo #(
        .DATA_WIDTH(DATA_WIDTH + 1),
        .DEPTH(FIFO_DEPTH),
        .SYNC_STAGES(2)
    ) u_rx_fifo (
        .wclk(rx_clk),
        .wrst_n(rx_rst_n),
        .winc(rx_fifo_winc),
        .wdata(rx_fifo_wdata),
        .wfull(rx_fifo_wfull),
        .rclk(m_axis_aclk),
        .rrst_n(m_axis_aresetn),
        .rinc(rx_fifo_rinc),
        .rdata(rx_fifo_rdata),
        .rempty(rx_fifo_rempty)
    );

    assign m_axis_tvalid = ~rx_fifo_rempty;
    assign m_axis_tdata  = rx_fifo_rdata[DATA_WIDTH-1:0];
    assign m_axis_tlast  = rx_fifo_rdata[DATA_WIDTH];

    always_ff @(posedge rx_clk or negedge rx_rst_n) begin
        if (!rx_rst_n)
            rx_frame_seen_sticky <= 1'b0;
        else if (rx_clear_pulse | rx_flush_pulse)
            rx_frame_seen_sticky <= 1'b0;
        else if (rx_par_valid)
            rx_frame_seen_sticky <= 1'b1;
    end

    assign rx_status_rx = {rx_busy, rx_frame_seen_sticky, rx_frame_overflow,
                           rx_frame_locked, rx_lock_stale, rx_lock};

    // -------------------------------------------------------------------------
    // Status into pclk
    // -------------------------------------------------------------------------
    cdc_sync #(.WIDTH(1)) u_tx_axis_ready (
        .clk(pclk), .rst_n(presetn), .d(s_axis_tready), .q(tx_axis_ready_p)
    );
    cdc_sync #(.WIDTH(1)) u_m_axis_valid (
        .clk(pclk), .rst_n(presetn), .d(m_axis_tvalid), .q(m_axis_valid_p)
    );
    cdc_sync #(.WIDTH(3)) u_tx_status (
        .clk(pclk), .rst_n(presetn), .d(tx_status_tx), .q(tx_status_p)
    );
    cdc_sync #(.WIDTH(6)) u_rx_status (
        .clk(pclk), .rst_n(presetn), .d(rx_status_rx), .q(rx_status_p)
    );

endmodule
