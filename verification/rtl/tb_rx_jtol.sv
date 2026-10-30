`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// Full digital RX-chain jitter harness.
//
// The Python side generates a 1 ps-resolution edge schedule representing the
// *sliced* analog RX waveform (ISI + AWGN + threshold included).  This TB feeds
// that binary stream into the complete `serdes_rx_digital` chain (oversampling
// CDR + receiver sampler + deserializer), locks the real CDR, and records
// `rx_serial_data` — i.e. the recovered stream after the CDR, not the channel
// input.  BER/bit-slip analysis is done in Python.
//
// Plusargs: +STIM=<path> +OUT=<path> +NBITS=<n>
// -----------------------------------------------------------------------------
module tb_rx_jtol #(
    parameter int OS         = 8,
    parameter int DATA_WIDTH = 8,
    parameter int WORDS      = 4,
    parameter int CDR_MODE   = 1,
    parameter int ALIGN_MODE = 0,
    parameter int MAX_BITS   = 20000
);

    logic clk;
    logic rst_n;
    logic enable;
    logic data_in;
    logic lock;
    logic lock_stale;
    logic sample_strobe;
    logic serial_data;
    logic serial_valid;
    logic clk_out;
    logic [WORDS-1:0][DATA_WIDTH-1:0] par_data;
    logic par_valid;
    logic busy;
    logic frame_locked;
    logic frame_valid;
    logic signed [12:0] freq_trim;

    logic rx_bits [0:MAX_BITS-1];

    int fd_stim;
    int fd_out;
    string stim_path;
    string out_path;
    int time_ps;
    int bit_value;
    int n_target;
    int recovered;
    int lock_wait;
    int lock_losses;
    logic lock_prev;
    logic locked_once;

    real half_ns;

    initial begin
        half_ns = 5.0;
        if (!$value$plusargs("HALF_NS=%f", half_ns))
            half_ns = 5.0;
    end

    always #(half_ns) clk = ~clk;

    serdes_rx_digital #(
        .DATA_WIDTH(DATA_WIDTH),
        .WORDS(WORDS),
        .LSB_FIRST(1'b1),
        .OVERSAMPLE(OS),
        .SYNC_STAGES(3),
        .LOCK_THRESH(8),
        .LOCK_STALE_BITS(32),
        .CDR_MODE(CDR_MODE),
        .CDR_BYPASS(1'b0),
        .ALIGN_MODE(ALIGN_MODE),
        .SYNC_LEN(8),
        .SYNC_PATTERN(8'hBC),
        .ADAPT_FRAC_BITS(12),
        .ADAPT_LOOP_GAIN(8)
    ) dut (
        .rx_clk(clk),
        .rx_rst_n(rst_n),
        .rx_enable(enable),
        .rx_flush(1'b0),
        .rx_align(1'b0),
        .rx_serial_in(data_in),
        .phase_offset('0),
        .rx_par_data(par_data),
        .rx_par_valid(par_valid),
        .rx_busy(busy),
        .rx_lock(lock),
        .rx_lock_stale(lock_stale),
        .rx_sample_strobe(sample_strobe),
        .rx_clk_out(clk_out),
        .rx_frame_locked(frame_locked),
        .rx_frame_valid(frame_valid),
        .rx_serial_data(serial_data),
        .rx_serial_valid(serial_valid),
        .rx_freq_trim(freq_trim)
    );

    always @(posedge clk) begin
        lock_prev <= lock;
        if (lock)
            locked_once <= 1'b1;
        if (locked_once && lock_prev && !lock)
            lock_losses <= lock_losses + 1;
    end

    initial begin
        data_in = 1'b0;
        wait (rst_n);
        if (!$value$plusargs("STIM=%s", stim_path)) begin
            $display("ERROR: +STIM=<path> is required");
            $finish;
        end
        fd_stim = $fopen(stim_path, "r");
        if (fd_stim == 0) begin
            $display("ERROR: cannot open stimulus %s", stim_path);
            $finish;
        end
        while ($fscanf(fd_stim, "%d %d\n", time_ps, bit_value) == 2) begin
            if (time_ps / 1000.0 > $realtime)
                #((time_ps / 1000.0) - $realtime);
            data_in = bit_value[0];
        end
        $fclose(fd_stim);
    end

    initial begin
        if (!$value$plusargs("OUT=%s", out_path)) begin
            $display("ERROR: +OUT=<path> is required");
            $finish;
        end
        if (!$value$plusargs("NBITS=%d", n_target))
            n_target = 4000;
        if (n_target > MAX_BITS)
            n_target = MAX_BITS;

        clk         = 1'b0;
        rst_n       = 1'b0;
        enable      = 1'b0;
        recovered   = 0;
        lock_wait   = 0;
        lock_losses = 0;
        locked_once = 1'b0;
        lock_prev   = 1'b0;

        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);
        enable = 1'b1;

        while (!lock && lock_wait < 100000) begin
            @(posedge clk);
            #1;
            lock_wait++;
        end
        if (!lock) begin
            $display("NO_LOCK lock_wait=%0d", lock_wait);
            $finish;
        end

        repeat (32) @(posedge clk);
        for (int cyc = 0; cyc < n_target * OS * 2 + 20000 && recovered < n_target; cyc++) begin
            @(posedge clk);
            #1;
            if (serial_valid) begin
                rx_bits[recovered] = serial_data;
                recovered++;
            end
        end

        fd_out = $fopen(out_path, "w");
        if (fd_out == 0) begin
            $display("ERROR: cannot open output %s", out_path);
            $finish;
        end
        for (int i = 0; i < recovered; i++)
            $fwrite(fd_out, "%0d\n", rx_bits[i]);
        $fclose(fd_out);

        $display("META recovered=%0d lock_wait=%0d lock_losses=%0d freq_trim=%0d",
                 recovered, lock_wait, lock_losses, freq_trim);
        $finish;
    end

endmodule
