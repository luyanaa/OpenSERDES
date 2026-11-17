`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// PIPE-Lite digital compatibility shell (PIPE_DIGITAL_COMPAT).
//
// This is NOT a PCIe/USB/SATA PHY.  It is a 16-bit PIPE-style digital interface
// on top of the Raw PCS: 8b/10b encode/decode, K-code passthrough, RxStatus
// error reporting, an elastic buffer with SKP insert/remove, PowerDown gating
// and a digital TxDetectRx -> PhyStatus response.
//
// Analog PIPE signals are exported as hooks and driven by digital stubs:
//   analog_tx_elec_idle / analog_rx_elec_idle / analog_tx_det_rx
// A real PMA can drive the same nets once it exists; until then the stub is
// what the qualification label PIPE_DIGITAL_COMPAT refers to.
//
// Wire format: two 8b/10b symbols per 20-bit Raw PCS word.  The PIPE word
// clock is clk / 20, so 16 data bits cross per word clock.
// -----------------------------------------------------------------------------
module serdes_pipe_lite_top #(
    parameter bit LSB_FIRST  = 1'b1,
    parameter int OVERSAMPLE = 1,
    parameter int CDR_MODE   = 2,
    parameter int SYNC_STAGES = 3
) (
    input  logic        clk,
    input  logic        rst_n,

    // TX PIPE interface
    input  logic [15:0] tx_data,
    input  logic [1:0]  tx_datak,
    input  logic        tx_valid,
    output logic        tx_ready,
    output logic        tx_serial_out,
    output logic        tx_serial_valid,
    output logic        tx_usr_clk,

    // RX PIPE interface
    input  logic        rx_serial_in,
    output logic [15:0] rx_data,
    output logic [1:0]  rx_datak,
    output logic        rx_valid,
    output logic [2:0]  rx_status,
    output logic        rx_rec_clk,

    // PIPE control
    input  logic [1:0]  power_down,
    input  logic        rate,
    input  logic        tx_elec_idle,
    input  logic        rx_elec_idle,
    input  logic        tx_detect_rx,
    output logic        phy_status,

    // Analog hooks (digital stubs until a PMA exists)
    output logic        analog_tx_elec_idle,
    output logic        analog_rx_elec_idle,
    output logic        analog_tx_det_rx,

    output logic        rx_lock
);

    localparam int PIPE_W = 20;
    localparam logic [2:0] ST_NORMAL   = 3'b000;
    localparam logic [2:0] ST_SKP      = 3'b001;
    localparam logic [2:0] ST_DETECT   = 3'b010;
    localparam logic [2:0] ST_DECODE   = 3'b011;
    localparam logic [2:0] ST_OVERFLOW = 3'b100;
    localparam logic [2:0] ST_UNDER    = 3'b101;
    localparam logic [2:0] ST_DISPARITY= 3'b110;

    // -------------------------------------------------------------------------
    // Raw PCS (20-bit encoded word)
    // -------------------------------------------------------------------------
    logic [PIPE_W-1:0] pcs_tx_data, pcs_rx_data;
    logic pcs_tx_valid, pcs_tx_ready, pcs_rx_valid;
    logic pcs_rx_frame_locked, pcs_rx_clk_out;

    serdes_raw_pcs_top #(
        .DATA_WIDTH(PIPE_W),
        .LSB_FIRST(LSB_FIRST),
        .OVERSAMPLE(OVERSAMPLE),
        .CDR_MODE(CDR_MODE),
        .SYNC_LEN(PIPE_W),
        // K28.5, K28.5 (RD- then RD+) = the PIPE COM word.
        .SYNC_PATTERN(PIPE_W'(20'hA0D7C)),
        .SYNC_STAGES(SYNC_STAGES)
    ) u_pcs (
        .clk(clk), .rst_n(rst_n),
        .tx_data(pcs_tx_data), .tx_valid(pcs_tx_valid), .tx_ready(pcs_tx_ready),
        .tx_serial_out(tx_serial_out), .tx_serial_valid(tx_serial_valid),
        .tx_usr_clk(tx_usr_clk),
        .rx_serial_in(rx_serial_in),
        .rx_data(pcs_rx_data), .rx_valid(pcs_rx_valid), .rx_rec_clk(rx_rec_clk),
        .rx_slide(1'b0), .rx_align_bypass(1'b0),
        .rx_lock(rx_lock), .rx_busy(),
        .rx_frame_locked(pcs_rx_frame_locked), .rx_clk_out(pcs_rx_clk_out)
    );

    // -------------------------------------------------------------------------
    // TX 8b/10b: two symbols per 20-bit word, running disparity chained by the
    // encoder across the two cycles.
    // -------------------------------------------------------------------------
    typedef enum logic [1:0] {E_IDLE, E_S0, E_S1, E_SEND} enc_state_t;
    enc_state_t enc_state_q;
    logic [15:0] tx_data_q;
    logic [1:0]  tx_datak_q;
    logic [9:0]  enc0_q, enc1_q;
    logic        enc_en;
    logic [7:0]  enc_8b;
    logic        enc_ctrl;
    logic [9:0]  enc_10b;
    logic        enc_err;

    assign tx_ready = (enc_state_q == E_IDLE) && (power_down == 2'b00);

    encoder_8b10b u_enc (
        .i_clk(clk), .i_reset_n(rst_n), .i_en(enc_en),
        .i_8b(enc_8b), .i_ctrl(enc_ctrl),
        .o_10b(enc_10b), .o_code_err(enc_err)
    );

    always_comb begin
        enc_en   = 1'b0;
        enc_8b   = 8'h00;
        enc_ctrl = 1'b0;
        case (enc_state_q)
            E_IDLE: if (tx_valid) begin
                enc_en   = 1'b1;
                enc_8b   = tx_data[7:0];
                enc_ctrl = tx_datak[0];
            end
            E_S0: begin
                enc_en   = 1'b1;
                enc_8b   = tx_data_q[15:8];
                enc_ctrl = tx_datak_q[1];
            end
            default: ;
        endcase
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            enc_state_q <= E_IDLE;
            tx_data_q   <= '0;
            tx_datak_q  <= '0;
            enc0_q      <= '0;
            enc1_q      <= '0;
            pcs_tx_data  <= '0;
            pcs_tx_valid <= 1'b0;
        end else begin
            pcs_tx_valid <= 1'b0;
            case (enc_state_q)
                E_IDLE: if (tx_valid) begin
                    tx_data_q  <= tx_data;
                    tx_datak_q <= tx_datak;
                    enc_state_q <= E_S0;
                end
                E_S0: begin
                    enc0_q <= enc_10b;
                    enc_state_q <= E_S1;
                end
                E_S1: begin
                    enc1_q <= enc_10b;
                    enc_state_q <= E_SEND;
                end
                E_SEND: begin
                    pcs_tx_data  <= {enc1_q, enc0_q};
                    pcs_tx_valid <= 1'b1;
                    if (pcs_tx_ready)
                        enc_state_q <= E_IDLE;
                end
                default: enc_state_q <= E_IDLE;
            endcase
        end
    end

    // -------------------------------------------------------------------------
    // RX 8b/10b: decode the two symbols, then write the word into the elastic
    // buffer.
    // -------------------------------------------------------------------------
    typedef enum logic [1:0] {D_IDLE, D_S0, D_S1, D_OUT} dec_state_t;
    dec_state_t dec_state_q;
    logic [PIPE_W-1:0] rx_word_q;
    logic [7:0] dec0_8b, dec1_8b;
    logic       dec0_ctrl, dec1_ctrl;
    logic       dec0_err, dec1_err, dec0_disp, dec1_disp;
    logic       dec_en;
    logic [9:0] dec_10b;
    logic [7:0] dec_8b;
    logic       dec_ctrl, dec_err, dec_disp;
    logic       eb_wr_en;
    logic [17:0] eb_wr_data, eb_rd_data;
    logic        eb_rd_valid;
    logic [2:0]  eb_status;
    logic [15:0] rx_data_q;
    logic [1:0]  rx_datak_q;
    logic        rx_valid_q;
    logic [2:0]  rx_status_q;
    logic        dec_error_q, dec_disp_q;

    // Hold the decoder in reset until the Raw PCS comma locks, so its running
    // disparity starts at the first aligned word instead of tracking the
    // pre-alignment garbage.
    wire dec_rst_n = rst_n & pcs_rx_frame_locked;

    decoder_8b10b u_dec (
        .i_clk(clk), .i_reset_n(dec_rst_n), .i_en(dec_en),
        .i_10b(dec_10b),
        .o_8b(dec_8b), .o_ctrl(dec_ctrl),
        .o_code_err(dec_err), .o_disp_err(dec_disp)
    );

    always_comb begin
        dec_en   = 1'b0;
        dec_10b  = 10'h000;
        case (dec_state_q)
            D_IDLE: if (pcs_rx_valid) begin
                dec_en  = 1'b1;
                dec_10b = pcs_rx_data[9:0];
            end
            D_S0: begin
                dec_en  = 1'b1;
                dec_10b = rx_word_q[19:10];
            end
            default: ;
        endcase
    end

    always_ff @(posedge clk or negedge dec_rst_n) begin
        if (!dec_rst_n) begin
            dec_state_q <= D_IDLE;
            rx_word_q   <= '0;
            dec0_8b     <= '0;
            dec1_8b     <= '0;
            dec0_ctrl   <= 1'b0;
            dec1_ctrl   <= 1'b0;
            dec0_err    <= 1'b0;
            dec1_err    <= 1'b0;
            dec0_disp   <= 1'b0;
            dec1_disp   <= 1'b0;
            eb_wr_en    <= 1'b0;
            eb_wr_data  <= '0;
            dec_error_q <= 1'b0;
            dec_disp_q  <= 1'b0;
        end else begin
            eb_wr_en <= 1'b0;
            case (dec_state_q)
                D_IDLE: if (pcs_rx_valid) begin
                    rx_word_q   <= pcs_rx_data;
                    dec_state_q <= D_S0;
                end
                D_S0: begin
                    dec0_8b   <= dec_8b;
                    dec0_ctrl <= dec_ctrl;
                    dec0_err  <= dec_err;
                    dec0_disp <= dec_disp;
                    dec_state_q <= D_S1;
                end
                D_S1: begin
                    dec1_8b   <= dec_8b;
                    dec1_ctrl <= dec_ctrl;
                    dec1_err  <= dec_err;
                    dec1_disp <= dec_disp;
                    dec_state_q <= D_OUT;
                end
                D_OUT: begin
                    eb_wr_en   <= pcs_rx_frame_locked;
                    eb_wr_data <= {dec1_ctrl, dec0_ctrl, dec1_8b, dec0_8b};
                    dec_error_q <= dec0_err | dec1_err;
                    dec_disp_q  <= dec0_disp | dec1_disp;
                    dec_state_q <= D_IDLE;
                end
                default: dec_state_q <= D_IDLE;
            endcase
        end
    end

    // Read tick: this single-clock wrapper runs the elastic buffer in
    // pass-through (one read per decoded word).  The SKP insert/remove path
    // is exercised by tb_pipe_elastic_buffer with an independent read rate.
    logic rd_tick;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            rd_tick <= 1'b0;
        else
            rd_tick <= eb_wr_en;
    end

    pipe_elastic_buffer #(.DEPTH(16), .HIGH_WM(15), .LOW_WM(0)) u_eb (
        .clk(clk), .rst_n(rst_n),
        .wr_en(eb_wr_en), .wr_data(eb_wr_data),
        .rd_tick(rd_tick), .rd_data(eb_rd_data),
        .rd_valid(eb_rd_valid), .status(eb_status)
    );

    // -------------------------------------------------------------------------
    // PIPE control: PowerDown gates the interface, TxDetectRx returns a
    // one-cycle PhyStatus with RxStatus=receiver detected.
    // -------------------------------------------------------------------------
    logic tx_detect_rx_q, phy_status_q;
    logic [2:0] phy_cnt_q;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            tx_detect_rx_q <= 1'b0;
            phy_status_q   <= 1'b0;
            phy_cnt_q      <= '0;
        end else begin
            tx_detect_rx_q <= tx_detect_rx;
            if (tx_detect_rx & ~tx_detect_rx_q)
                phy_cnt_q <= 3'd4;
            else if (phy_cnt_q != 0)
                phy_cnt_q <= phy_cnt_q - 1'b1;
            phy_status_q <= (phy_cnt_q != 0);
        end
    end

    assign phy_status = phy_status_q;
    assign analog_tx_elec_idle = tx_elec_idle | (power_down != 2'b00);
    assign analog_rx_elec_idle = rx_elec_idle | (power_down == 2'b11);
    assign analog_tx_det_rx    = tx_detect_rx;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rx_data_q   <= '0;
            rx_datak_q  <= '0;
            rx_valid_q  <= 1'b0;
            rx_status_q <= ST_NORMAL;
        end else begin
            rx_valid_q <= 1'b0;
            rx_status_q <= ST_NORMAL;
            if (power_down == 2'b00 && eb_rd_valid) begin
                rx_data_q  <= eb_rd_data[15:0];
                rx_datak_q <= eb_rd_data[17:16];
                rx_valid_q <= 1'b1;
                if (dec_error_q)
                    rx_status_q <= ST_DECODE;
                else if (dec_disp_q)
                    rx_status_q <= ST_DISPARITY;
                else
                    rx_status_q <= eb_status;
            end
            if (phy_status_q)
                rx_status_q <= ST_DETECT;
        end
    end

    assign rx_data   = rx_data_q;
    assign rx_datak  = rx_datak_q;
    assign rx_valid  = rx_valid_q;
    assign rx_status = rx_status_q;

endmodule
