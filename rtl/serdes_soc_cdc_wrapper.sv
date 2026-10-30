`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Multi-clock SoC wrapper: APB CSR + AXIS async FIFOs + source-synchronous link
// + APB-controlled PRBS BIST with loopback and coherent counter snapshots.
//
// Domains:
//   pclk        : APB CSR, synchronized status, BIST snapshot read port
//   s_axis_aclk : TX AXIS input, TX FIFO write side
//   tx_clk      : TX FIFO read / serializer / PRBS BIST generator + checker
//   rx_clk      : RX core / RX FIFO write side
//   m_axis_aclk : RX AXIS output, RX FIFO read side
//
// TX AXIS frame = WORDS+1 beats: comma (0xBC) + WORDS payload words.
// RX AXIS frame = WORDS payload words.
//
// CSR map (word addresses):
//   0       CONTROL          RW: [0] tx_enable, [1] rx_enable, [2] rx_flush,
//                                [3] tx_clear, [4] rx_clear
//   1..6                     RW scratch / reserved
//   7       STATUS           RO: link status (status_i, layout unchanged)
//   8       BIST_CTRL        RW: [0] enable, [1] start, [2] clear, [3] inject,
//                                [4] loopback, [5] snapshot
//   9                        RW reserved
//   10      BIST_ERROR_COUNT RO: snapshot of the tx_clk error counter
//   11      BIST_BIT_COUNT   RO: snapshot of the tx_clk bit counter
//   12      BIST_STATUS      RO: [0] error (snapshot), [1] running (snapshot,
//                                start & enable), [2] snap_valid (live pclk),
//                                [3] loopback (live), [4] inject (live),
//                                [7:5] PRBS width code
//   13..15                   RW reserved
//
// BIST placement: the PRBS generator and checker run in tx_clk; the serial
// stream is continuous at one bit per clock and must not cross clock domains.
// The BIST control bits cross pclk -> tx_clk with one cdc_sync per level, and
// the tx_clk counters return through bist_snapshot (stable-bus handshake) so
// software always reads a coherent word.  BIST_STATUS combines the live pclk
// bits (snap_valid/loopback/inject) with snapshot bits in one APB read.
//
// Loopback (BIST_CTRL[4]) is the deterministic self-check mode: the checker
// consumes the tx_clk serial output directly.  Checking the external
// rx_serial_in requires tx_clk and rx_clk to be frequency/phase related,
// because the checker samples the cdc_sync'd rx_serial_in in the tx_clk
// domain.
//
// General control/status are static levels or pulses synchronized with
// cdc_sync.  The multi-bit link status bus is synchronized bit-by-bit for
// observability; software must treat it as independent status bits, not a
// coherent snapshot (use the BIST snapshot registers for coherent counters).
// -----------------------------------------------------------------------------
module serdes_soc_cdc_wrapper #(
    parameter int DATA_WIDTH  = 8,
    parameter int WORDS       = 4,
    parameter bit LSB_FIRST   = 1'b1,
    parameter int SYNC_LEN    = 8,
    parameter logic [SYNC_LEN-1:0] SYNC_PATTERN = 'hBC,
    parameter int ADAPT_FRAC_BITS = 12,
    parameter int FIFO_DEPTH  = 16,
    parameter bit CRC_ENABLE  = 1'b0,
    parameter int CRC_WIDTH   = 16,
    parameter logic [CRC_WIDTH-1:0] CRC_POLY = 16'h1021,
    parameter logic [CRC_WIDTH-1:0] CRC_INIT = '1,
    parameter int BIST_PRBS_WIDTH = 7,
    parameter logic [BIST_PRBS_WIDTH-1:0] BIST_SEED = '1,
    parameter int OVERSAMPLE = 1,
    parameter int CDR_MODE = 2,
    parameter bit TRAINING_ENABLE = 1'b0,
    parameter int TRAIN_DWELL_CYCLES = 64,
    parameter int TRAIN_ERROR_WIDTH = 16,
    parameter int PHASE_CTRL_W = (OVERSAMPLE <= 1) ? 1 : $clog2(OVERSAMPLE)
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
    output logic [15:0][31:0]                 csr_regs,

    // Optional training control/telemetry (rx_clk domain).  The error count
    // is supplied by the RX-side scoreboard/PRBS checker; this wrapper does
    // not create a synthetic error source.
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic                              train_start,
    input  logic [TRAIN_ERROR_WIDTH-1:0]      train_error_count,
    /* verilator lint_on UNUSEDSIGNAL */
    output logic                              train_locked,
    output logic                              train_busy,
    output logic                              train_retrain,
    output logic [PHASE_CTRL_W-1:0]           train_phase,
    output logic [PHASE_CTRL_W-1:0]           train_best_phase,
    output logic [TRAIN_ERROR_WIDTH-1:0]      train_best_errors
);

    localparam int CRC_BYTES = (CRC_WIDTH + DATA_WIDTH - 1) / DATA_WIDTH;
    localparam int TX_WORDS = WORDS + 1 + (CRC_ENABLE ? CRC_BYTES : 0);
    localparam int RX_WORDS = WORDS + (CRC_ENABLE ? CRC_BYTES : 0);

    // PRBS width reported in BIST_STATUS[7:5].
    localparam logic [2:0] BIST_WIDTH_CODE = (BIST_PRBS_WIDTH == 7)  ? 3'b001 :
                                             (BIST_PRBS_WIDTH == 15) ? 3'b010 :
                                             (BIST_PRBS_WIDTH == 31) ? 3'b011 : 3'b000;

    // -------------------------------------------------------------------------
    // APB control / status
    // -------------------------------------------------------------------------
    logic [31:0] status;
    logic [15:0][31:0] csr_ro;

    logic tx_enable_p, rx_enable_p, rx_flush_p, tx_clear_p, rx_clear_p;
    logic bist_enable_p, bist_start_p, bist_clear_p, bist_inject_p, bist_loopback_p;
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

    assign bist_enable_p   = csr_regs[8][0];
    assign bist_start_p    = csr_regs[8][1];
    assign bist_clear_p    = csr_regs[8][2];
    assign bist_inject_p   = csr_regs[8][3];
    assign bist_loopback_p = csr_regs[8][4];

    assign status = {21'b0, m_axis_valid_p, rx_status_p, tx_status_p, tx_axis_ready_p};

    apb_csr #(
        .NUM_REGS(16),
        .DATA_WIDTH(32),
        .ADDR_WIDTH(16),
        .STATUS_IDX(7),
        .RO_MASK(16'b0111_1100_0000_0000)
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
        .status_i(status),
        .ro_i(csr_ro)
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
    // BIST control into the link clock domains
    // -------------------------------------------------------------------------
    logic bist_enable_tx, bist_start_tx, bist_clear_tx, bist_inject_tx, bist_loopback_tx;
    logic bist_enable_rx, bist_loopback_rx;

    cdc_sync #(.WIDTH(1)) u_bist_enable_tx (
        .clk(tx_clk), .rst_n(tx_rst_n), .d(bist_enable_p), .q(bist_enable_tx)
    );
    cdc_sync #(.WIDTH(1)) u_bist_start_tx (
        .clk(tx_clk), .rst_n(tx_rst_n), .d(bist_start_p), .q(bist_start_tx)
    );
    cdc_sync #(.WIDTH(1)) u_bist_clear_tx (
        .clk(tx_clk), .rst_n(tx_rst_n), .d(bist_clear_p), .q(bist_clear_tx)
    );
    cdc_sync #(.WIDTH(1)) u_bist_inject_tx (
        .clk(tx_clk), .rst_n(tx_rst_n), .d(bist_inject_p), .q(bist_inject_tx)
    );
    cdc_sync #(.WIDTH(1)) u_bist_loopback_tx (
        .clk(tx_clk), .rst_n(tx_rst_n), .d(bist_loopback_p), .q(bist_loopback_tx)
    );

    cdc_sync #(.WIDTH(1)) u_bist_enable_rx (
        .clk(rx_clk), .rst_n(rx_rst_n), .d(bist_enable_p), .q(bist_enable_rx)
    );
    cdc_sync #(.WIDTH(1)) u_bist_loopback_rx (
        .clk(rx_clk), .rst_n(rx_rst_n), .d(bist_loopback_p), .q(bist_loopback_rx)
    );

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
    logic tx_core_enable, tx_core_serial_out, tx_core_serial_valid;
    logic tx_adapter_ready, tx_pipe_tvalid, tx_pipe_tlast;
    logic [DATA_WIDTH-1:0] tx_pipe_tdata;

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

    generate
        if (CRC_ENABLE) begin : g_tx_crc
            /* verilator lint_off PINCONNECTEMPTY */
            axis_crc_inserter #(
                .DATA_WIDTH(DATA_WIDTH), .CRC_WIDTH(CRC_WIDTH),
                .POLY(CRC_POLY), .INIT(CRC_INIT)
            ) u_tx_crc (
                .clk(tx_clk), .rst_n(tx_rst_n), .enable(tx_core_enable),
                .s_axis_tvalid(tx_fifo_valid), .s_axis_tready(tx_fifo_ready),
                .s_axis_tdata(tx_fifo_rdata[DATA_WIDTH-1:0]),
                .s_axis_tlast(tx_fifo_rdata[DATA_WIDTH]),
                .m_axis_tvalid(tx_pipe_tvalid), .m_axis_tready(tx_adapter_ready),
                .m_axis_tdata(tx_pipe_tdata), .m_axis_tlast(tx_pipe_tlast),
                .m_axis_tuser(), .crc_word()
            );
            /* verilator lint_on PINCONNECTEMPTY */
        end else begin : g_tx_no_crc
            assign tx_fifo_ready = tx_adapter_ready;
            assign tx_pipe_tvalid = tx_fifo_valid;
            assign tx_pipe_tdata  = tx_fifo_rdata[DATA_WIDTH-1:0];
            assign tx_pipe_tlast  = tx_fifo_rdata[DATA_WIDTH];
        end
    endgenerate

    axis_tx_adapter #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(TX_WORDS)
    ) u_tx_axis (
        .clk(tx_clk),
        .rst_n(tx_rst_n),
        .clear(tx_clear_pulse),
        .s_axis_tvalid(tx_pipe_tvalid),
        .s_axis_tready(tx_adapter_ready),
        .s_axis_tdata(tx_pipe_tdata),
        .s_axis_tlast(tx_pipe_tlast),
        .in_valid(tx_in_valid),
        .in_ready(tx_in_ready),
        .in_word(tx_in_word),
        .frame_error(tx_frame_error)
    );

    assign tx_core_enable = tx_enable_tx & ~bist_enable_tx;

    serializer_stream #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(TX_WORDS),
        .LSB_FIRST(LSB_FIRST)
    ) u_serializer (
        .clk(tx_clk),
        .rst_n(tx_rst_n),
        .enable(tx_core_enable),
        .in_valid(tx_in_valid),
        .in_ready(tx_in_ready),
        .in_word(tx_in_word),
        .serial_out(tx_core_serial_out),
        .serial_valid(tx_core_serial_valid),
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
    // PRBS BIST: generator/checker in tx_clk, loopback muxing, snapshot bridge.
    // The checker input never feeds tx_serial_out, so there is no loop.
    // -------------------------------------------------------------------------
    logic bist_serial_out, bist_check_in, rx_serial_in_tx;
    logic bist_error, bist_error_pulse_tx, bist_error_pulse_rx;
    logic [31:0] bist_error_count, bist_bit_count;
    logic [TRAIN_ERROR_WIDTH-1:0] bist_train_error_count;
    logic bist_running;

    assign tx_serial_out   = bist_enable_tx ? bist_serial_out : tx_core_serial_out;
    assign tx_serial_valid = bist_enable_tx ? bist_start_tx   : tx_core_serial_valid;

    assign bist_running = bist_start_tx & bist_enable_tx;

    cdc_sync #(.WIDTH(1)) u_rx_serial_in_tx (
        .clk(tx_clk), .rst_n(tx_rst_n), .d(rx_serial_in), .q(rx_serial_in_tx)
    );

    assign bist_check_in = (bist_loopback_tx ? tx_serial_out : rx_serial_in_tx)
                           ^ bist_inject_tx;

    serdes_bist #(
        .WIDTH(BIST_PRBS_WIDTH),
        .SEED(BIST_SEED)
    ) u_bist (
        .clk(tx_clk),
        .rst_n(tx_rst_n),
        .start(bist_start_tx),
        .clear(bist_clear_tx),
        .serial_out(bist_serial_out),
        .serial_in(bist_check_in),
        .error(bist_error),
        .error_pulse(bist_error_pulse_tx),
        .error_count(bist_error_count),
        .bit_count(bist_bit_count)
    );

    cdc_sync #(.WIDTH(1)) u_bist_error_pulse_rx (
        .clk(rx_clk), .rst_n(rx_rst_n),
        .d(bist_error_pulse_tx), .q(bist_error_pulse_rx)
    );
    always_ff @(posedge rx_clk or negedge rx_rst_n) begin
        if (!rx_rst_n)
            bist_train_error_count <= '0;
        else if (rx_clear_pulse | rx_flush_pulse)
            bist_train_error_count <= '0;
        else if (bist_error_pulse_rx)
            bist_train_error_count <= bist_train_error_count + 1'b1;
    end
    /* verilator lint_off UNUSEDSIGNAL */
    logic [TRAIN_ERROR_WIDTH-1:0] train_error_total;
    assign train_error_total = train_error_count + bist_train_error_count;
    /* verilator lint_on UNUSEDSIGNAL */

    logic bist_snap_valid, bist_error_snap, bist_running_snap;
    logic [31:0] bist_error_count_snap, bist_bit_count_snap;

    bist_snapshot #(.WIDTH(32)) u_bist_snapshot (
        .pclk(pclk),
        .presetn(presetn),
        .req(csr_regs[8][5]),
        .valid(bist_snap_valid),
        .error_count_o(bist_error_count_snap),
        .bit_count_o(bist_bit_count_snap),
        .error_o(bist_error_snap),
        .running_o(bist_running_snap),
        .clk(tx_clk),
        .rst_n(tx_rst_n),
        .error_count_i(bist_error_count),
        .bit_count_i(bist_bit_count),
        .error_i(bist_error),
        .running_i(bist_running)
    );

    // APB read-only view: BIST registers (bits 10-12) and training telemetry
    // (bit 13 = best_errors[7:0], bit 14 = lock/busy/retrain/phase).
    always_comb begin
        csr_ro = '0;
        csr_ro[10] = bist_error_count_snap;
        csr_ro[11] = bist_bit_count_snap;
        csr_ro[12] = {24'b0, BIST_WIDTH_CODE, bist_inject_p, bist_loopback_p,
                      bist_snap_valid, bist_running_snap, bist_error_snap};
        csr_ro[13] = 32'(train_best_errors);
        csr_ro[14] = '0;
        csr_ro[14][0] = train_locked;
        csr_ro[14][1] = train_busy;
        csr_ro[14][2] = train_retrain;
        csr_ro[14][3 +: PHASE_CTRL_W] = train_phase;
        csr_ro[14][3 + PHASE_CTRL_W +: PHASE_CTRL_W] = train_best_phase;
        csr_ro[14][7] = rx_crc_error_sticky;
    end

    // -------------------------------------------------------------------------
    // RX path: RX core -> adapter -> AXIS async FIFO
    // -------------------------------------------------------------------------
    logic [RX_WORDS-1:0][DATA_WIDTH-1:0] rx_par_data;
    logic rx_frame_valid, rx_par_valid, rx_busy;
    logic rx_lock, rx_lock_stale, rx_frame_locked, rx_frame_overflow;
    logic rx_axis_tvalid, rx_axis_tready, rx_axis_tlast;
    logic [DATA_WIDTH-1:0] rx_axis_tdata;
    /* verilator lint_off UNUSEDSIGNAL */
    logic rx_adapter_tvalid, rx_adapter_tready, rx_adapter_tlast, rx_adapter_tuser;
    /* verilator lint_on UNUSEDSIGNAL */
    logic [DATA_WIDTH-1:0] rx_adapter_tdata;
    logic rx_crc_error, rx_crc_error_sticky;
    logic [DATA_WIDTH:0] rx_fifo_wdata, rx_fifo_rdata;
    logic rx_fifo_wfull, rx_fifo_rempty, rx_fifo_winc, rx_fifo_rinc;
    logic rx_frame_seen_sticky;
    logic rx_core_enable, rx_core_serial_in, tx_serial_out_rx;
    logic [PHASE_CTRL_W-1:0] phase_offset;

    // BIST loopback into the RX core: the TX serial output is a static-rate
    // level in rx_clk terms for loopback testing, so a 2FF sync is sufficient;
    // the RX core is gated off whenever the BIST owns the TX datapath.
    cdc_sync #(.WIDTH(1)) u_tx_serial_out_rx (
        .clk(rx_clk), .rst_n(rx_rst_n), .d(tx_serial_out), .q(tx_serial_out_rx)
    );

    assign rx_core_enable    = rx_enable_rx & ~bist_enable_rx;
    assign rx_core_serial_in = bist_loopback_rx ? tx_serial_out_rx : rx_serial_in;

    generate
        if (TRAINING_ENABLE) begin : g_train
            /* verilator lint_off PINCONNECTEMPTY */
            link_training_fsm #(
                .PHASE_STEPS(OVERSAMPLE), .THRESH_STEPS(1),
                .DWELL_CYCLES(TRAIN_DWELL_CYCLES),
                .ERROR_WIDTH(TRAIN_ERROR_WIDTH),
                .RETRAIN_ERR_THRESH(4), .RETRAIN_WINDOW(256)
            ) u_train (
                .clk(rx_clk), .rst_n(rx_rst_n),
                .enable(rx_enable_rx), .start(train_start),
                .error_count(train_error_total),
                .phase_sel(phase_offset), .thresh_sel(),
                .phase_we(), .thresh_we(),
                .busy(train_busy), .locked(train_locked), .retrain(train_retrain),
                .state(), .best_phase(train_best_phase), .best_thresh(),
                .best_errors(train_best_errors)
            );
            /* verilator lint_on PINCONNECTEMPTY */
            assign train_phase = phase_offset;
        end else begin : g_no_train
            assign phase_offset      = '0;
            assign train_locked      = 1'b0;
            assign train_busy        = 1'b0;
            assign train_retrain     = 1'b0;
            assign train_phase       = '0;
            assign train_best_phase  = '0;
            assign train_best_errors = '0;
        end
    endgenerate

    serdes_rx_digital #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(RX_WORDS),
        .LSB_FIRST(LSB_FIRST),
        .OVERSAMPLE(OVERSAMPLE),
        .CDR_MODE(CDR_MODE),
        .CDR_BYPASS(OVERSAMPLE == 1),
        .ALIGN_MODE(2),
        .SYNC_LEN(SYNC_LEN),
        .SYNC_PATTERN(SYNC_PATTERN),
        .ADAPT_FRAC_BITS(ADAPT_FRAC_BITS)
    ) u_rx (
        .rx_clk(rx_clk),
        .rx_rst_n(rx_rst_n),
        .rx_enable(rx_core_enable),
        .rx_flush(rx_flush_pulse),
        .rx_align(1'b0),
        .rx_serial_in(rx_core_serial_in),
        .phase_offset(phase_offset),
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
        .WORDS(RX_WORDS),
        .PAYLOAD_WORDS(WORDS)
    ) u_rx_axis (
        .clk(rx_clk),
        .rst_n(rx_rst_n),
        .clear(rx_clear_pulse | rx_flush_pulse),
        .par_valid(rx_frame_valid),
        .par_data(rx_par_data),
        .m_axis_tvalid(rx_adapter_tvalid),
        .m_axis_tready(rx_adapter_tready),
        .m_axis_tdata(rx_adapter_tdata),
        .m_axis_tlast(rx_adapter_tlast),
        .m_axis_tuser(rx_adapter_tuser),
        .frame_overflow(rx_frame_overflow)
    );

    generate
        if (CRC_ENABLE) begin : g_rx_crc
            /* verilator lint_off PINCONNECTEMPTY */
            axis_crc_checker #(
                .DATA_WIDTH(DATA_WIDTH), .CRC_WIDTH(CRC_WIDTH),
                .POLY(CRC_POLY), .INIT(CRC_INIT)
            ) u_rx_crc (
                .clk(rx_clk), .rst_n(rx_rst_n), .enable(rx_core_enable),
                .s_axis_tvalid(rx_adapter_tvalid), .s_axis_tready(rx_adapter_tready),
                .s_axis_tdata(rx_adapter_tdata), .s_axis_tlast(rx_adapter_tlast),
                .s_axis_tuser(rx_adapter_tuser),
                .m_axis_tvalid(rx_axis_tvalid), .m_axis_tready(rx_axis_tready),
                .m_axis_tdata(rx_axis_tdata), .m_axis_tlast(rx_axis_tlast),
                .crc_error(rx_crc_error), .crc_ok()
            );
            /* verilator lint_on PINCONNECTEMPTY */
        end else begin : g_rx_no_crc
            assign rx_adapter_tready = rx_axis_tready;
            assign rx_axis_tvalid = rx_adapter_tvalid;
            assign rx_axis_tdata  = rx_adapter_tdata;
            assign rx_axis_tlast  = rx_adapter_tlast;
            assign rx_crc_error   = 1'b0;
        end
    endgenerate

    always_ff @(posedge rx_clk or negedge rx_rst_n) begin
        if (!rx_rst_n)
            rx_crc_error_sticky <= 1'b0;
        else if (rx_flush_pulse | rx_clear_pulse)
            rx_crc_error_sticky <= 1'b0;
        else if (rx_crc_error)
            rx_crc_error_sticky <= 1'b1;
    end

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
