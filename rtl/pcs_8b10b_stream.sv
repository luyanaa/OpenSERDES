`timescale 1ns/1ps

/* verilator lint_off DECLFILENAME */

// -----------------------------------------------------------------------------
// Streaming 8b/10b codec stages.
//
// These two modules are the "glue" used when an 8-bit AXIS datapath has to be
// carried over a 10-bit 8b/10b line without framing the whole packet in
// parallel registers:
//
//   pcs_8b10b_tx_stream : one byte per handshake in, one 10-bit symbol out.
//                         Beat 0 of every WORDS-beat block is encoded as
//                         K28.5 (the frame comma), the rest as data.
//   pcs_8b10b_rx_stream : one deserialized symbol group (SYMBOLS wide, the
//                         trailing comma included) in, PAYLOAD decoded bytes
//                         out.  The trailing comma is decoded but not
//                         forwarded: decoding it keeps the running disparity
//                         locked to the transmitter across frames.
//
// Running disparity is held inside pcs_8b10b_tx / pcs_8b10b_rx and therefore
// spans frames; `enable` low freezes the datapath, `flush` returns the beat
// counter / collector to the start of a block.
// -----------------------------------------------------------------------------

module pcs_8b10b_tx_stream #(
    parameter int WORDS = 5
) (
    input  logic             clk,
    input  logic             rst_n,
    input  logic             enable,
    input  logic             flush,
    input  logic             s_valid,
    output logic             s_ready,
    input  logic [7:0]       s_data,
    output logic             m_valid,
    input  logic             m_ready,
    output logic [9:0]       m_code,
    output logic             code_error
);

    localparam int IDX_W = (WORDS <= 1) ? 1 : $clog2(WORDS);
    localparam logic [IDX_W-1:0] LAST_IDX = IDX_W'(WORDS-1);

    logic [IDX_W-1:0] idx_q;
    logic [9:0]       code_q;
    logic             code_vld_q;
    logic             err_q;
    logic             enc_fire;
    logic [9:0]       enc_code;
    logic             enc_valid;
    logic             enc_error;
    logic             ctrl;

    // The comma is the first beat of every block.
    assign ctrl    = (idx_q == '0);
    assign enc_fire = s_valid & s_ready;
    assign s_ready  = enable & (m_ready | ~code_vld_q);
    assign m_valid  = code_vld_q;
    assign m_code   = code_q;
    assign code_error = err_q;

    pcs_8b10b_tx u_enc (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .valid(enc_fire), .data(s_data), .ctrl(ctrl),
        .code(enc_code), .code_valid(enc_valid), .code_error(enc_error)
    );

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            idx_q      <= '0;
            code_q     <= '0;
            code_vld_q <= 1'b0;
            err_q      <= 1'b0;
        end else if (!enable || flush) begin
            idx_q      <= '0;
            code_vld_q <= 1'b0;
        end else begin
            if (enc_error)
                err_q <= 1'b1;

            // A freshly encoded symbol wins over a symbol consumed in the
            // same cycle: the register must hold the new code.
            if (enc_valid) begin
                code_q     <= enc_code;
                code_vld_q <= 1'b1;
            end else if (m_valid && m_ready) begin
                code_vld_q <= 1'b0;
            end

            if (enc_fire)
                idx_q <= (idx_q == LAST_IDX) ? '0 : idx_q + 1'b1;
        end
    end

endmodule


module pcs_8b10b_rx_stream #(
    // Symbols per received block.
    parameter int SYMBOLS = 5,
    // 1 when the block ends with the next frame's comma symbol.
    parameter bit DROP_LAST = 1'b0
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  enable,
    input  logic                  flush,
    input  logic [SYMBOLS-1:0][9:0] par_data,
    input  logic                  par_valid,
    input  logic                  rd_init,
    input  logic                  rd_load,
    output logic [PAYLOAD-1:0][7:0] payload,
    output logic [PAYLOAD-1:0]    charisk,
    output logic                  frame_valid,
    output logic                  code_error,
    // High when no group is in flight: a disparity seed is only applied now,
    // so a spurious comma mid-group cannot swallow a symbol's valid pulse.
    output logic                  idle
);

    localparam int PAYLOAD = SYMBOLS - (DROP_LAST ? 1 : 0);
    localparam int IDX_W   = (SYMBOLS <= 1) ? 1 : $clog2(SYMBOLS);
    localparam logic [IDX_W-1:0] LAST_SYM = IDX_W'(SYMBOLS-1);

    typedef enum logic [1:0] { S_IDLE, S_DEC, S_WAIT, S_OUT } state_t;
    state_t state_q;

    logic [SYMBOLS-1:0][9:0] sym_q;
    logic [PAYLOAD-1:0][7:0] pay_q;
    logic [PAYLOAD-1:0]      k_q;
    logic [IDX_W-1:0]        idx_q;
    logic [2:0]              wait_cnt_q;
    logic                    err_q;

    logic [9:0] dec_code;
    logic       dec_valid;
    logic [7:0] dec_data;
    logic       dec_ctrl;
    logic       dec_data_valid;
    logic       dec_code_error;
    logic       dec_disp_error;

    assign payload     = pay_q;
    assign charisk     = k_q;
    assign dec_code    = sym_q[idx_q];
    assign dec_valid   = enable && (state_q == S_DEC);
    assign code_error  = err_q;
    assign idle        = (state_q == S_IDLE);

    pcs_8b10b_rx u_dec (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .valid(dec_valid), .code(dec_code),
        .rd_init(rd_init), .rd_load(rd_load),
        .data(dec_data), .ctrl(dec_ctrl),
        .data_valid(dec_data_valid), .code_error(dec_code_error),
        .disp_error(dec_disp_error)
    );

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state_q     <= S_IDLE;
            sym_q       <= '0;
            pay_q       <= '0;
            k_q         <= '0;
            idx_q       <= '0;
            wait_cnt_q  <= '0;
            err_q       <= 1'b0;
            frame_valid <= 1'b0;
        end else begin
            frame_valid <= 1'b0;

            if (dec_code_error || dec_disp_error)
                err_q <= 1'b1;

            if (!enable || flush) begin
                state_q    <= S_IDLE;
                idx_q      <= '0;
                wait_cnt_q <= '0;
            end else begin
                case (state_q)
                    S_IDLE: begin
                        wait_cnt_q <= '0;
                        if (par_valid) begin
                            sym_q   <= par_data;
                            idx_q   <= '0;
                            state_q <= S_DEC;
                        end
                    end
                    S_DEC: begin
                        wait_cnt_q <= '0;
                        state_q    <= S_WAIT;
                    end
                    S_WAIT: begin
                        wait_cnt_q <= wait_cnt_q + 1'b1;
                        // A symbol that never returns (enable removed, or a
                        // realign in the middle of the group) must not wedge
                        // the collector: drop the group and wait for a new
                        // boundary.
                        if (!dec_data_valid && wait_cnt_q == 3'd7) begin
                            state_q <= S_IDLE;
                            idx_q   <= '0;
                        end
                        if (dec_data_valid) begin
                            // A dropped trailing symbol is decoded for the
                            // running disparity only, never forwarded.
                            if (!(DROP_LAST && idx_q == LAST_SYM)) begin
                                pay_q[idx_q] <= dec_data;
                                k_q[idx_q]   <= dec_ctrl;
                            end
                            if (idx_q == LAST_SYM) begin
                                state_q <= S_OUT;
                            end else begin
                                idx_q   <= idx_q + 1'b1;
                                state_q <= S_DEC;
                            end
                        end
                    end
                    S_OUT: begin
                        frame_valid <= 1'b1;
                        state_q     <= S_IDLE;
                    end
                    default: state_q <= S_IDLE;
                endcase
            end
        end
    end

endmodule

/* verilator lint_on DECLFILENAME */
