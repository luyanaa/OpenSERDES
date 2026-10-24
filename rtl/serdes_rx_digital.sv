`timescale 1ns/1ps

// Digital RX endpoint: oversampling CDR + deserializer + reset synchronizer.
// This wrapper can be synthesized on a different PDK from the TX endpoint.
module serdes_rx_digital #(
    parameter int DATA_WIDTH  = 32,
    parameter int WORDS       = 8,
    parameter bit LSB_FIRST   = 1'b1,
    parameter int OVERSAMPLE  = 4,
    parameter int SYNC_STAGES = 2,
    parameter int LOCK_THRESH = 8,
    parameter int LOCK_RELEASE = 4,
    parameter bit CDR_BYPASS  = 1'b0
) (
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
    output logic                              rx_sample_strobe,
    output logic                              rx_clk_out
);

    logic rx_rst_n_sync;
    logic cdr_data_out;
    logic cdr_sample_strobe;
    logic cdr_lock;

    reset_sync #(.STAGES(2)) u_reset_sync (
        .clk(rx_clk),
        .rst_n_in(rx_rst_n),
        .rst_n_out(rx_rst_n_sync)
    );

    generate
        if (CDR_BYPASS) begin : g_bypass
            logic bypass_valid_q;

            // Receiver-only mode: the external rx_clk is the bit clock and
            // the receiver's digital sampling boundary is a one-bit retimer.
            receiver_sampler u_sampler (
                .clk(rx_clk),
                .rst_n(rx_rst_n_sync),
                .enable(rx_enable),
                .d(rx_serial_in),
                .q(cdr_data_out)
            );

            always_ff @(posedge rx_clk or negedge rx_rst_n_sync) begin
                if (!rx_rst_n_sync)
                    bypass_valid_q <= 1'b0;
                else
                    bypass_valid_q <= rx_enable;
            end

            assign cdr_sample_strobe = bypass_valid_q;
            assign cdr_lock          = 1'b1;
            assign rx_clk_out        = rx_clk;
        end else begin : g_cdr
            cdr_oversampling #(
                .OVERSAMPLE(OVERSAMPLE),
                .SYNC_STAGES(SYNC_STAGES),
                .LOCK_THRESH(LOCK_THRESH),
                .LOCK_RELEASE(LOCK_RELEASE),
                .LOCK_DECAY(1)
            ) u_cdr (
                .clk(rx_clk),
                .rst_n(rx_rst_n_sync),
                .enable(rx_enable),
                .data_in(rx_serial_in),
                .data_out(cdr_data_out),
                .sample_strobe(cdr_sample_strobe),
                .clk_out(rx_clk_out),
                .lock(cdr_lock)
            );
        end
    endgenerate

    deserializer #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(LSB_FIRST)
    ) u_deserializer (
        .clk(rx_clk),
        .rst_n(rx_rst_n_sync),
        .enable(rx_enable),
        .align(rx_align),
        .flush(rx_flush),
        .serial_in(cdr_data_out),
        .serial_valid(cdr_sample_strobe & cdr_lock),
        .par_data(rx_par_data),
        .par_valid(rx_par_valid),
        .busy(rx_busy)
    );

    assign rx_lock          = cdr_lock;
    assign rx_sample_strobe = cdr_sample_strobe;

endmodule
