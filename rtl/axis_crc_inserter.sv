`timescale 1ns/1ps

// AXI4-Stream CRC inserter.
//
// The input frame is payload beats terminated by tlast.  The output is the
// same payload (without tlast) followed by ceil(CRC_WIDTH/DATA_WIDTH) CRC
// beats; only the final CRC beat carries tlast.  Data and CRC are little-
// endian slices: byte 0 is the least-significant CRC byte.
//
// This is a framing primitive, not a complete PCS.  Payload length, sequence
// IDs and sync words remain the caller's responsibility.
module axis_crc_inserter #(
    parameter int  DATA_WIDTH = 8,
    parameter int  CRC_WIDTH  = 16,
    parameter logic [CRC_WIDTH-1:0] POLY = 16'h1021,
    parameter logic [CRC_WIDTH-1:0] INIT = '1,
    parameter bit  LSB_FIRST  = 1'b0,
    parameter bit  REFLECT_OUT = 1'b0,
    parameter logic [CRC_WIDTH-1:0] XOR_OUT = '0
) (
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic                    enable,
    input  logic                    s_axis_tvalid,
    output logic                    s_axis_tready,
    input  logic [DATA_WIDTH-1:0]   s_axis_tdata,
    input  logic                    s_axis_tlast,
    output logic                    m_axis_tvalid,
    input  logic                    m_axis_tready,
    output logic [DATA_WIDTH-1:0]   m_axis_tdata,
    output logic                    m_axis_tlast,
    output logic                    m_axis_tuser,
    output logic [CRC_WIDTH-1:0]    crc_word
);

    localparam int CRC_BYTES = (CRC_WIDTH + DATA_WIDTH - 1) / DATA_WIDTH;
    localparam int IDX_W = (CRC_BYTES <= 1) ? 1 : $clog2(CRC_BYTES);

    logic [IDX_W-1:0]  crc_idx_q;
    logic               first_q;
    logic               state_crc_q;
    logic               s_fire;
    logic               crc_start;
    logic               crc_end;
    logic [CRC_WIDTH-1:0] crc_comb;

    /* verilator lint_off PINCONNECTEMPTY */
    crc_frame #(
        .DATA_WIDTH(DATA_WIDTH), .CRC_WIDTH(CRC_WIDTH), .POLY(POLY),
        .INIT(INIT), .LSB_FIRST(LSB_FIRST),
        .REFLECT_OUT(REFLECT_OUT), .XOR_OUT(XOR_OUT)
    ) u_crc (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .frame_start(crc_start), .frame_end(crc_end),
        .data_valid(s_fire), .data(s_axis_tdata),
        .crc(crc_comb), .crc_raw(), .crc_valid()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    assign s_fire = s_axis_tvalid && s_axis_tready;
    assign crc_start = s_fire && first_q;
    assign crc_end = s_fire && s_axis_tlast;
    assign s_axis_tready = enable && !state_crc_q && m_axis_tready;
    assign m_axis_tvalid = enable && (state_crc_q || s_axis_tvalid);
    assign m_axis_tdata = state_crc_q
                          ? DATA_WIDTH'(crc_comb >> (crc_idx_q * DATA_WIDTH))
                          : s_axis_tdata;
    assign m_axis_tlast = state_crc_q && (crc_idx_q == IDX_W'(CRC_BYTES - 1));
    // tuser marks the last payload beat.  tlast remains reserved for the
    // final CRC beat so the composite stream is still one AXIS frame.
    assign m_axis_tuser = s_axis_tvalid && s_axis_tlast && !state_crc_q;
    assign crc_word = crc_comb;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            crc_idx_q  <= '0;
            first_q    <= 1'b1;
            state_crc_q <= 1'b0;
        end else if (!enable) begin
            crc_idx_q  <= '0;
            first_q    <= 1'b1;
            state_crc_q <= 1'b0;
        end else begin
            if (!state_crc_q && s_fire) begin
                first_q <= 1'b0;
                if (s_axis_tlast) begin
                    state_crc_q <= 1'b1;
                    crc_idx_q   <= '0;
                end
            end else if (state_crc_q && m_axis_tready) begin
                if (crc_idx_q == IDX_W'(CRC_BYTES - 1)) begin
                    state_crc_q <= 1'b0;
                    first_q     <= 1'b1;
                    crc_idx_q   <= '0;
                end else begin
                    crc_idx_q <= crc_idx_q + 1'b1;
                end
            end
        end
    end

endmodule
