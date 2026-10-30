`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Generic framed CRC accumulator.
//
// The CRC is updated for every asserted `data_valid` word.  `frame_start`
// loads `INIT`; `frame_end` marks the last word and raises `crc_valid` for one
// cycle.  The CRC is serial (one bit at a time) so arbitrary DATA_WIDTH and
// CRC_WIDTH values are supported without a generated XOR matrix.
//
// For CRC-16/CCITT-FALSE use:
//   CRC_WIDTH=16, POLY=16'h1021, INIT=16'hFFFF, LSB_FIRST=0,
//   REFLECT_OUT=0, XOR_OUT=16'h0000.
// For CRC-32/ISO-HDLC use:
//   CRC_WIDTH=32, POLY=32'h04C11DB7, INIT=32'hFFFFFFFF, LSB_FIRST=1,
//   REFLECT_OUT=1, XOR_OUT=32'hFFFFFFFF.
//
// `crc` is the unmasked accumulator; the final reflected/XORed value is
// presented when `crc_valid` is asserted for one cycle.  If a consumer needs
// the raw state during a frame, use the `crc_raw` output.
// -----------------------------------------------------------------------------
module crc_frame #(
    parameter int  DATA_WIDTH   = 8,
    parameter int  CRC_WIDTH    = 16,
    parameter logic [CRC_WIDTH-1:0] POLY = 16'h1021,
    parameter logic [CRC_WIDTH-1:0] INIT = '1,
    parameter bit  LSB_FIRST    = 1'b0,
    parameter bit  REFLECT_OUT  = 1'b0,
    parameter logic [CRC_WIDTH-1:0] XOR_OUT = '0
) (
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic                    enable,
    input  logic                    frame_start,
    input  logic                    frame_end,
    input  logic                    data_valid,
    input  logic [DATA_WIDTH-1:0]   data,
    output logic [CRC_WIDTH-1:0]    crc,
    output logic [CRC_WIDTH-1:0]    crc_raw,
    output logic                    crc_valid
);

    logic [CRC_WIDTH-1:0] crc_q;
    logic [CRC_WIDTH-1:0] next_crc;
    logic [CRC_WIDTH-1:0] next_crc_init;
    logic                 valid_q;

    function automatic logic [CRC_WIDTH-1:0] reflect(
        input logic [CRC_WIDTH-1:0] value
    );
        logic [CRC_WIDTH-1:0] result;
        result = '0;
        for (int i = 0; i < CRC_WIDTH; i++)
            result[i] = value[CRC_WIDTH-1-i];
        return result;
    endfunction

    function automatic logic [CRC_WIDTH-1:0] step(
        input logic [CRC_WIDTH-1:0] crc_in,
        input logic [DATA_WIDTH-1:0] data_in
    );
        logic [CRC_WIDTH-1:0] work;
        logic din;
        work = crc_in;
        for (int i = 0; i < DATA_WIDTH; i++) begin
            if (LSB_FIRST)
                din = data_in[i];
            else
                din = data_in[DATA_WIDTH-1-i];
            if (work[CRC_WIDTH-1] ^ din)
                work = {work[CRC_WIDTH-2:0], 1'b0} ^ POLY;
            else
                work = {work[CRC_WIDTH-2:0], 1'b0};
        end
        return work;
    endfunction

    assign next_crc      = step(crc_q, data);
    assign next_crc_init = step(INIT, data);

    assign crc_raw = crc_q;
    assign crc = REFLECT_OUT ? (reflect(crc_q) ^ XOR_OUT) : (crc_q ^ XOR_OUT);
    assign crc_valid = valid_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            crc_q   <= INIT;
            valid_q <= 1'b0;
        end else if (!enable) begin
            crc_q   <= INIT;
            valid_q <= 1'b0;
        end else if (frame_start) begin
            // A frame may start and carry its first data word in the same
            // cycle.  This is required by AXIS-style streaming adapters.
            if (data_valid) begin
                crc_q   <= next_crc_init;
                valid_q <= frame_end;
            end else begin
                crc_q   <= INIT;
                valid_q <= 1'b0;
            end
        end else if (data_valid) begin
            crc_q   <= next_crc;
            valid_q <= frame_end;
        end else if (frame_end) begin
            valid_q <= 1'b1;
        end else begin
            valid_q <= 1'b0;
        end
    end

endmodule
