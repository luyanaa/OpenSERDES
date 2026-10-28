`timescale 1ns/1ps

// Deserializer parallel frame -> AXI4-Stream master.
//
// On par_valid the frame is captured in a local bank and emitted as WORDS
// beats.  m_axis_tlast marks the last word.  If a new frame arrives while the
// previous one is still pending (and its last beat is not being consumed in
// this cycle), frame_overflow is set and the new frame is dropped.
module axis_rx_adapter #(
    parameter int DATA_WIDTH = 32,
    parameter int WORDS      = 8
) (
    input  logic                              clk,
    input  logic                              rst_n,
    input  logic                              clear,
    input  logic                              par_valid,
    input  logic [WORDS-1:0][DATA_WIDTH-1:0]  par_data,
    output logic                              m_axis_tvalid,
    input  logic                              m_axis_tready,
    output logic [DATA_WIDTH-1:0]             m_axis_tdata,
    output logic                              m_axis_tlast,
    output logic                              frame_overflow
);

    localparam int WORD_W = (WORDS <= 1) ? 1 : $clog2(WORDS);
    localparam logic [WORD_W-1:0] LAST_WORD = WORD_W'(WORDS-1);

    logic [WORDS-1:0][DATA_WIDTH-1:0] frame_q;
    logic [WORD_W-1:0] beat_q;
    logic              pending_q;
    logic              overflow_q;
    logic              accept_new;
    logic              beat_fire;
    logic              last_fire;
    logic [WORDS-1:0]  beat_select;
    logic [DATA_WIDTH-1:0] axis_data;

    generate
        for (genvar w = 0; w < WORDS; w++) begin : g_beat
            assign beat_select[w] = (beat_q == WORD_W'(w));
        end
    endgenerate

    always_comb begin
        accept_new = par_valid && (!pending_q || last_fire);
    end

    always_comb begin
        axis_data = '0;
        for (int w = 0; w < WORDS; w++) begin
            if (beat_select[w])
                axis_data = frame_q[w];
        end
    end

    assign beat_fire = m_axis_tvalid && m_axis_tready;
    assign last_fire = beat_fire && m_axis_tlast;

    assign m_axis_tvalid = pending_q;
    assign m_axis_tdata  = axis_data;
    assign m_axis_tlast  = (beat_q == LAST_WORD) && pending_q;
    assign frame_overflow = overflow_q;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            frame_q    <= '0;
            beat_q     <= '0;
            pending_q  <= 1'b0;
            overflow_q <= 1'b0;
        end else if (clear) begin
            beat_q     <= '0;
            pending_q  <= 1'b0;
            overflow_q <= 1'b0;
        end else begin
            // A frame may be accepted in the same cycle that the previous
            // frame's last beat is consumed.  Prioritize the accept so the
            // later beat-consumption path cannot clear pending_q for the new
            // frame.  Mid-frame consumption with a new frame is still an
            // overflow, but the current beat must advance.
            if (par_valid && !accept_new)
                overflow_q <= 1'b1;

            if (accept_new) begin
                frame_q   <= par_data;
                beat_q    <= '0;
                pending_q <= 1'b1;
            end else if (last_fire) begin
                pending_q <= 1'b0;
                beat_q    <= '0;
            end

            if (beat_fire && !last_fire)
                beat_q <= beat_q + 1'b1;
        end
    end

endmodule
