`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Legacy-compatible serializer adapter.
//
// The original `serializer_unit_cell_1` interface is kept so existing digital
// integration can switch to the new core without changing its port list.
// The implementation is the new `serializer` core, not the old RTL.
//
// The legacy block had no load pulse: READY was a level that both started and
// gated the frame.  Here READY starts a frame when the core is idle and then
// acts as the serial enable.  COMPLETE is the core's one-cycle frame_done.
// COUNT/SAMPLE_COUNT are compatibility observability counters, not the core's
// internal state.
//
// Do not compile this file together with legacy/rtl/serializer_unit_cell_1.v:
// both define the same module name.
// -----------------------------------------------------------------------------
module serializer_unit_cell_1 (
    input  logic        CLK,
    input  logic        RESET,
    input  logic        READY,
    output logic        SERIAL_OUT,
    output logic        INTERNAL_FINISH,
    output logic        COMPLETE,
    input  logic [31:0] PAR_IN1,
    input  logic [31:0] PAR_IN2,
    input  logic [31:0] PAR_IN3,
    input  logic [31:0] PAR_IN4,
    input  logic [31:0] PAR_IN5,
    input  logic [31:0] PAR_IN6,
    input  logic [31:0] PAR_IN7,
    input  logic [31:0] PAR_IN8,
    output logic [5:0]  COUNT,
    output logic [3:0]  SAMPLE_COUNT
);

    localparam int DATA_WIDTH = 32;
    localparam int WORDS      = 8;

    logic [WORDS-1:0][DATA_WIDTH-1:0] par_data;
    logic core_load;
    logic core_ready;
    logic core_serial_out;
    logic core_serial_valid;
    logic core_busy;
    logic core_frame_done;

    assign par_data = {PAR_IN8, PAR_IN7, PAR_IN6, PAR_IN5,
                       PAR_IN4, PAR_IN3, PAR_IN2, PAR_IN1};
    assign core_load = READY & core_ready;

    serializer #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(1'b1),
        .CAPTURE_MODE(1'b0)
    ) u_core (
        .clk(CLK),
        .rst_n(RESET),
        .enable(READY),
        .load(core_load),
        .par_data(par_data),
        .ready(core_ready),
        .serial_out(core_serial_out),
        .serial_valid(core_serial_valid),
        .busy(core_busy),
        .frame_done(core_frame_done)
    );

    assign SERIAL_OUT      = core_serial_out;
    assign COMPLETE        = core_frame_done;
    assign INTERNAL_FINISH = core_busy && core_serial_valid && (COUNT == 6'd31);

    always_ff @(posedge CLK or negedge RESET) begin
        if (!RESET) begin
            COUNT        <= '0;
            SAMPLE_COUNT <= '0;
        end else if (core_load) begin
            COUNT        <= '0;
            SAMPLE_COUNT <= '0;
        end else if (core_serial_valid) begin
            if (COUNT == 6'd31) begin
                COUNT <= '0;
                if (SAMPLE_COUNT < 4'd7)
                    SAMPLE_COUNT <= SAMPLE_COUNT + 1'b1;
                else
                    SAMPLE_COUNT <= 4'd8;
            end else begin
                COUNT <= COUNT + 1'b1;
            end
        end
    end

endmodule
