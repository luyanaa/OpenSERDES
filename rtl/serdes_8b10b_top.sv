`timescale 1ns/1ps

// 8b/10b link wrapper.
//
// Payload words are bytes; one byte is encoded to a 10-bit symbol before
// serialization and decoded after deserialization.  The codec is sequenced
// one symbol per clock so running disparity is preserved across words and
// frames.  Sync/comma framing is external in this wrapper (rx.align=external).
/* verilator lint_off UNUSEDPARAM */
module serdes_8b10b_top #(
    parameter int DATA_WIDTH      = 8,
    parameter int WORDS           = 8,
    parameter bit LSB_FIRST       = 1'b1,
    parameter bit CAPTURE_MODE    = 1'b0,
    parameter int OVERSAMPLE      = 1,
    parameter int SYNC_STAGES     = 3,
    parameter int LOCK_THRESH     = 8,
    parameter int LOCK_STALE_BITS = 16,
    parameter int CDR_MODE        = 2,
    parameter bit CDR_BYPASS      = 1'b1,
    parameter int ALIGN_MODE      = 0,
    parameter int SYNC_LEN        = 8,
    parameter logic [SYNC_LEN-1:0] SYNC_PATTERN = 'hBC,
    parameter int ADAPT_FRAC_BITS = 12,
    parameter int ADAPT_LOOP_GAIN = 8
)
/* verilator lint_on UNUSEDPARAM */
(
    input  logic                              tx_clk,
    input  logic                              tx_rst_n,
    input  logic                              tx_enable,
    input  logic                              tx_load,
    input  logic [WORDS-1:0][DATA_WIDTH-1:0]  tx_par_data,
    output logic                              tx_ready,
    output logic                              tx_serial_out,
    output logic                              tx_serial_valid,
    output logic                              tx_busy,
    output logic                              tx_frame_done,

    input  logic                              rx_clk,
    input  logic                              rx_rst_n,
    input  logic                              rx_enable,
    input  logic                              rx_flush,
    input  logic                              rx_align,
    input  logic                              rx_serial_in,
    output logic [WORDS-1:0][DATA_WIDTH-1:0]  rx_par_data,
    output logic                              rx_par_valid,
    output logic                              rx_busy,
    output logic                              rx_lock,
    output logic                              rx_lock_stale,
    output logic                              rx_sample_strobe,
    output logic                              rx_clk_out,
    output logic                              rx_frame_locked,
    output logic                              rx_frame_valid,
    output logic                              rx_serial_data,
    output logic                              rx_serial_valid,
    output logic signed [ADAPT_FRAC_BITS:0]   rx_freq_trim,
    output logic                              code_error
);
    localparam int LINE_WIDTH = 10;
    localparam int IDX_W = (WORDS <= 1) ? 1 : $clog2(WORDS);

    logic [WORDS-1:0][LINE_WIDTH-1:0] tx_line_q;
    logic [WORDS-1:0][LINE_WIDTH-1:0] rx_line_q;
    logic [WORDS-1:0][DATA_WIDTH-1:0] tx_payload_q;
    logic [WORDS-1:0][DATA_WIDTH-1:0] rx_payload_q;
    logic [IDX_W-1:0] tx_idx_q, rx_idx_q;

    logic tx_core_load, tx_core_ready, tx_core_done;
    logic tx_core_busy, tx_core_serial_valid;
    logic rx_core_par_valid;
    logic [WORDS-1:0][LINE_WIDTH-1:0] rx_core_line;
    logic rx_core_busy, rx_core_frame_locked;
    logic [DATA_WIDTH-1:0] enc_data;
    logic [LINE_WIDTH-1:0] enc_code, dec_code;
    logic enc_valid, enc_code_valid, enc_error;
    logic dec_valid, dec_data_valid, dec_code_error, dec_disp_error;
    logic [DATA_WIDTH-1:0] dec_data;
    /* verilator lint_off UNUSEDSIGNAL */
    logic dec_ctrl;
    /* verilator lint_on UNUSEDSIGNAL */

    typedef enum logic [2:0] {
        T_IDLE, T_ENC, T_ENC_WAIT, T_LOAD, T_BUSY
    } tx_state_t;
    typedef enum logic [2:0] {
        R_IDLE, R_DEC, R_DEC_WAIT, R_OUT
    } rx_state_t;
    tx_state_t tx_state_q;
    rx_state_t rx_state_q;

    // ---------------------------------------------------------------------
    // Payload -> 10-bit symbols, one byte per cycle.
    // ---------------------------------------------------------------------
    assign enc_valid = (tx_state_q == T_ENC);
    assign enc_data  = tx_payload_q[tx_idx_q];
    logic enc_ctrl;
    assign enc_ctrl = 1'b0;

    pcs_8b10b_tx u_enc (
        .clk(tx_clk), .rst_n(tx_rst_n), .enable(tx_enable),
        .valid(enc_valid), .data(enc_data), .ctrl(enc_ctrl),
        .code(enc_code), .code_valid(enc_code_valid), .code_error(enc_error)
    );

    assign tx_ready = (tx_state_q == T_IDLE) && tx_core_ready;

    always_ff @(posedge tx_clk or negedge tx_rst_n) begin
        if (!tx_rst_n) begin
            tx_state_q  <= T_IDLE;
            tx_idx_q    <= '0;
            tx_payload_q <= '0;
            tx_line_q   <= '0;
            tx_core_load <= 1'b0;
        end else if (!tx_enable) begin
            tx_state_q  <= T_IDLE;
            tx_idx_q    <= '0;
            tx_core_load <= 1'b0;
        end else begin
            tx_core_load <= 1'b0;
            case (tx_state_q)
                T_IDLE: begin
                    if (tx_load && tx_core_ready) begin
                        tx_payload_q <= tx_par_data;
                        tx_idx_q     <= '0;
                        tx_state_q   <= T_ENC;
                    end
                end
                T_ENC: tx_state_q <= T_ENC_WAIT;
                T_ENC_WAIT: begin
                    if (enc_code_valid) begin
                        tx_line_q[tx_idx_q] <= enc_code;
                        if (tx_idx_q == IDX_W'(WORDS-1)) begin
                            tx_state_q <= T_LOAD;
                        end else begin
                            tx_idx_q   <= tx_idx_q + 1'b1;
                            tx_state_q <= T_ENC;
                        end
                    end
                end
                T_LOAD: begin
                    tx_core_load <= 1'b1;
                    tx_state_q   <= T_BUSY;
                end
                T_BUSY: begin
                    if (tx_core_done)
                        tx_state_q <= T_IDLE;
                end
                default: tx_state_q <= T_IDLE;
            endcase
        end
    end

    serdes_tx_digital #(
        .DATA_WIDTH(LINE_WIDTH), .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST), .CAPTURE_MODE(CAPTURE_MODE)
    ) u_tx (
        .tx_clk(tx_clk), .tx_rst_n(tx_rst_n), .tx_enable(tx_enable),
        .tx_load(tx_core_load), .tx_par_data(tx_line_q), .tx_ready(tx_core_ready),
        .tx_serial_out(tx_serial_out), .tx_serial_valid(tx_core_serial_valid),
        .tx_busy(tx_core_busy), .tx_frame_done(tx_core_done)
    );
    assign tx_busy = (tx_state_q != T_IDLE) || tx_core_busy;
    assign tx_frame_done = tx_core_done;
    assign tx_serial_valid = tx_core_serial_valid;

    // ---------------------------------------------------------------------
    // 10-bit symbols -> payload bytes, one symbol per cycle.
    // ---------------------------------------------------------------------
    assign dec_valid = (rx_state_q == R_DEC);
    assign dec_code  = rx_line_q[rx_idx_q];

    pcs_8b10b_rx u_dec (
        .clk(rx_clk), .rst_n(rx_rst_n), .enable(rx_enable),
        .valid(dec_valid), .code(dec_code), .data(dec_data), .ctrl(dec_ctrl),
        .data_valid(dec_data_valid), .code_error(dec_code_error),
        .disp_error(dec_disp_error)
    );

    assign code_error = dec_code_error | dec_disp_error | enc_error;

    always_ff @(posedge rx_clk or negedge rx_rst_n) begin
        if (!rx_rst_n) begin
            rx_state_q <= R_IDLE;
            rx_idx_q   <= '0;
            rx_line_q  <= '0;
            rx_payload_q <= '0;
            rx_par_valid <= 1'b0;
            rx_frame_valid <= 1'b0;
            rx_frame_locked <= 1'b0;
        end else begin
            rx_par_valid   <= 1'b0;
            rx_frame_valid <= 1'b0;
            rx_frame_locked <= rx_core_frame_locked;
            if (rx_flush) begin
                rx_state_q <= R_IDLE;
                rx_idx_q   <= '0;
            end else begin
                case (rx_state_q)
                    R_IDLE: begin
                        if (rx_core_par_valid) begin
                            rx_line_q <= rx_core_line;
                            rx_idx_q  <= '0;
                            rx_state_q <= R_DEC;
                        end
                    end
                    R_DEC: rx_state_q <= R_DEC_WAIT;
                    R_DEC_WAIT: begin
                        if (dec_data_valid) begin
                            rx_payload_q[rx_idx_q] <= dec_data;
                            if (rx_idx_q == IDX_W'(WORDS-1)) begin
                                rx_state_q <= R_OUT;
                            end else begin
                                rx_idx_q   <= rx_idx_q + 1'b1;
                                rx_state_q <= R_DEC;
                            end
                        end
                    end
                    R_OUT: begin
                        rx_par_data  <= rx_payload_q;
                        rx_par_valid <= 1'b1;
                        rx_frame_valid <= 1'b1;
                        rx_state_q   <= R_IDLE;
                    end
                    default: rx_state_q <= R_IDLE;
                endcase
            end
        end
    end

    serdes_rx_digital #(
        .DATA_WIDTH(LINE_WIDTH), .WORDS(WORDS), .LSB_FIRST(LSB_FIRST),
        .OVERSAMPLE(OVERSAMPLE), .SYNC_STAGES(SYNC_STAGES),
        .LOCK_THRESH(LOCK_THRESH), .LOCK_STALE_BITS(LOCK_STALE_BITS),
        .CDR_MODE(CDR_MODE), .CDR_BYPASS(CDR_BYPASS), .ALIGN_MODE(ALIGN_MODE),
        .SYNC_LEN(SYNC_LEN), .SYNC_PATTERN(SYNC_PATTERN),
        .ADAPT_FRAC_BITS(ADAPT_FRAC_BITS), .ADAPT_LOOP_GAIN(ADAPT_LOOP_GAIN)
    ) u_rx (
        .rx_clk(rx_clk), .rx_rst_n(rx_rst_n), .rx_enable(rx_enable),
        .rx_flush(rx_flush), .rx_align(rx_align), .rx_serial_in(rx_serial_in),
        .phase_offset('0),
        .rx_par_data(rx_core_line), .rx_par_valid(rx_core_par_valid),
        .rx_busy(rx_core_busy), .rx_lock(rx_lock), .rx_lock_stale(rx_lock_stale),
        .rx_sample_strobe(rx_sample_strobe), .rx_clk_out(rx_clk_out),
        /* verilator lint_off PINCONNECTEMPTY */
        .rx_frame_locked(rx_core_frame_locked), .rx_frame_valid(),
        .rx_serial_data(rx_serial_data), .rx_serial_valid(rx_serial_valid),
        .rx_freq_trim(rx_freq_trim)
    );
    /* verilator lint_on PINCONNECTEMPTY */
    assign rx_busy = rx_core_busy || (rx_state_q != R_IDLE);
endmodule
