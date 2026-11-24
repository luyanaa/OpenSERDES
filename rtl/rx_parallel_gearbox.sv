`timescale 1ns/1ps
`default_nettype none
// -----------------------------------------------------------------------------
// Quarter-rate RX gearbox: 4 bits per quarter-rate cycle -> DATA_W-bit words.
//
// Input: one 4-UI group per clk_q cycle.  Bit order inside a group is UI
// order: data_q[0] is the earliest UI and is pushed first (it becomes the
// lowest bit index of the word stream).
//
// Elastic repair: the decimated loop reports the content-window shifts that
// the ppm drift and the rotation walk produce (see cdr_decimated_loop).  Each
// event; the gearbox holds the repair for the pipeline latency and then, at
// the shifted group's boundary:
//
//   slip_drop c > 0    the content revisited c bits (net sampling advance,
//                      data slower): skip the c leading bits of the group -
//                      the first c bits repeat the previous group's tail.
//   slip_insert c > 0  the content skipped c bits (net sampling delay, data
//                      faster): prepend c copies of the last streamed bit
//                      before the group - the same convention an elastic
//                      buffer uses for clock-tolerance compensation.
//
// Both repairs keep the streamed bit count matched to the local clk_q rate,
// so the word stream stays bit-continuous.  A real deployment funnels this
// into a proper elastic FIFO with a reclaimed read clock; for v1 the
// drop/insert is applied directly in the assembly shift register.
// -----------------------------------------------------------------------------
module rx_parallel_gearbox #(
    parameter int DATA_W = 16            // multiple of 4
) (
    input  wire               clk_q,
    input  wire               rst_n,
    input  wire               enable,
    input  wire [3:0]         data_q,      // UI order, data_q[0] earliest
    input  wire               slip_evt,    // 1-cycle elastic event
    input  wire [1:0]         slip_drop,   // duplicated bits to drop
    input  wire [1:0]         slip_insert, // skipped bits to fill
    output reg  [DATA_W-1:0]  rx_word,
    output reg                word_valid
);

    localparam int BITS  = (DATA_W < 4) ? 4 : DATA_W;
    localparam int CNT_W = (BITS <= 1) ? 1 : $clog2(BITS);

    reg [BITS-1:0]  shreg_q;
    reg [CNT_W-1:0] fill_q;
    reg             last_bit_q;
    reg [1:0]       pend_drop_q, pend_ins_q;
    reg [1:0]       pend_cnt_q;   // cycles until the shifted group arrives
    reg             pend_v_q;

    // Pending repair armed by the event, fired after PIPE_DLY cycles (the
    // shifted group reaches the push stage that many cycles later).
    localparam int PIPE_DLY = 3;
    wire arm   = enable && slip_evt;
    wire fire  = enable && pend_v_q && (pend_cnt_q == (PIPE_DLY-1));

    // Bits to push this cycle, stream order (index 0 first).
    reg [6:0] push_bits;
    int       push_n;
    int       d, n;

    always_comb begin
        push_n = enable ? 4 : 0;
        push_bits[0] = data_q[0];
        push_bits[1] = data_q[1];
        push_bits[2] = data_q[2];
        push_bits[3] = data_q[3];
        if (fire) begin
            if (pend_drop_q != 2'b00) begin
                d = pend_drop_q;            // 1..3 leading bits skipped
                for (n = 0; n < 4 - d; n++)
                    push_bits[n] = data_q[n + d];
                push_n = 4 - d;
            end else if (pend_ins_q != 2'b00) begin
                d = pend_ins_q;             // 1..3 repeats prepended
                for (n = 0; n < d; n++)
                    push_bits[n] = last_bit_q;
                push_bits[d]   = data_q[0];
                push_bits[d+1] = data_q[1];
                push_bits[d+2] = data_q[2];
                push_bits[d+3] = data_q[3];
                push_n = 4 + d;
            end
        end
    end

    always_ff @(posedge clk_q or negedge rst_n) begin
        if (!rst_n) begin
            shreg_q    <= '0;
            fill_q     <= '0;
            last_bit_q <= 1'b0;
            pend_drop_q<= 2'b00;
            pend_ins_q <= 2'b00;
            pend_cnt_q <= 2'b00;
            pend_v_q   <= 1'b0;
            rx_word    <= '0;
            word_valid <= 1'b0;
        end else begin
            word_valid <= 1'b0;
            // Arm / release the pending repair.
            if (pend_v_q && pend_cnt_q != 2'b11)
                pend_cnt_q <= pend_cnt_q + 2'b01;
            if (arm) begin
                pend_drop_q <= slip_drop;
                pend_ins_q  <= slip_insert;
                pend_cnt_q  <= 2'b00;
                pend_v_q    <= (slip_drop != 2'b00) || (slip_insert != 2'b00);
            end else if (fire) begin
                pend_v_q <= 1'b0;
            end
            if (push_n > 0) begin
                // Shadow the assembly register with blocking assignments so a
                // word completing mid-sequence sees this cycle's earlier bits.
                reg [BITS-1:0] shreg_n;
                int            c;
                shreg_n = shreg_q;
                c       = fill_q;
                for (int i = 0; i < push_n; i++) begin
                    if (c == BITS - 1) begin
                        shreg_n[BITS-1] = push_bits[i];
                        rx_word         <= shreg_n;
                        word_valid      <= 1'b1;
                        shreg_n         = '0;
                        c               = 0;
                    end else begin
                        shreg_n[c] = push_bits[i];
                        c          = c + 1;
                    end
                end
                shreg_q    <= shreg_n;
                fill_q     <= CNT_W'(c);
                last_bit_q <= push_bits[push_n-1];
            end
        end
    end

endmodule
