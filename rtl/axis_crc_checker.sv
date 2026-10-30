`timescale 1ns/1ps

// AXI4-Stream CRC checker for the frame format produced by
// axis_crc_inserter: payload beats terminated by tlast, then CRC beats with
// tlast on the final CRC beat.  Payload is forwarded unchanged; CRC beats are
// consumed and compared against the locally computed CRC.
module axis_crc_checker #(
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
    input  logic                    s_axis_tuser,
    output logic                    m_axis_tvalid,
    input  logic                    m_axis_tready,
    output logic [DATA_WIDTH-1:0]   m_axis_tdata,
    output logic                    m_axis_tlast,
    output logic                    crc_error,
    output logic                    crc_ok
);

    localparam int CRC_BYTES = (CRC_WIDTH + DATA_WIDTH - 1) / DATA_WIDTH;
    localparam int IDX_W = (CRC_BYTES <= 1) ? 1 : $clog2(CRC_BYTES);

    logic [IDX_W-1:0]    crc_idx_q;
    logic                first_q;
    logic                state_crc_q;
    logic                s_fire;
    logic                crc_start;
    logic                crc_end;
    logic                crc_data_valid;
    logic [CRC_WIDTH-1:0] crc_comb;
    logic [CRC_WIDTH-1:0] expected_q;
    logic                error_q;
    logic                ok_q;

    /* verilator lint_off PINCONNECTEMPTY */
    crc_frame #(
        .DATA_WIDTH(DATA_WIDTH), .CRC_WIDTH(CRC_WIDTH), .POLY(POLY),
        .INIT(INIT), .LSB_FIRST(LSB_FIRST),
        .REFLECT_OUT(REFLECT_OUT), .XOR_OUT(XOR_OUT)
    ) u_crc (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .frame_start(crc_start), .frame_end(crc_end),
        .data_valid(crc_data_valid), .data(s_axis_tdata),
        .crc(crc_comb), .crc_raw(), .crc_valid()
    );
    /* verilator lint_on PINCONNECTEMPTY */

    assign s_fire = s_axis_tvalid && s_axis_tready;
    assign crc_start = s_fire && first_q && !state_crc_q;
    assign crc_end = s_fire && s_axis_tuser && !state_crc_q;
    assign crc_data_valid = s_fire && !state_crc_q;
    assign s_axis_tready = enable && (!state_crc_q ? m_axis_tready : 1'b1);
    assign m_axis_tvalid = enable && !state_crc_q && s_axis_tvalid;
    assign m_axis_tdata = s_axis_tdata;
    assign m_axis_tlast = s_axis_tuser && !state_crc_q;
    assign crc_error = error_q;
    assign crc_ok = ok_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            crc_idx_q  <= '0;
            first_q    <= 1'b1;
            state_crc_q <= 1'b0;
            expected_q <= '0;
            error_q    <= 1'b0;
            ok_q       <= 1'b0;
        end else if (!enable) begin
            crc_idx_q  <= '0;
            first_q    <= 1'b1;
            state_crc_q <= 1'b0;
            expected_q <= '0;
            error_q    <= 1'b0;
            ok_q       <= 1'b0;
        end else begin
            error_q <= 1'b0;
            ok_q    <= 1'b0;
            if (!state_crc_q && s_fire) begin
                first_q <= 1'b0;
                if (s_axis_tuser) begin
                    state_crc_q <= 1'b1;
                    crc_idx_q   <= '0;
                    expected_q  <= '0;
                end
            end else if (state_crc_q && s_fire) begin
                expected_q <= expected_q |
                              (CRC_WIDTH'(s_axis_tdata) << (crc_idx_q * DATA_WIDTH));
                if (s_axis_tlast || (crc_idx_q == IDX_W'(CRC_BYTES - 1))) begin
                    if ((expected_q |
                         (CRC_WIDTH'(s_axis_tdata) << (crc_idx_q * DATA_WIDTH))) != crc_comb)
                        error_q <= 1'b1;
                    else
                        ok_q <= 1'b1;
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
