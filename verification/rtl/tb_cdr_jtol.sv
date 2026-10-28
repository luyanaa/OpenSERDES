`timescale 1ns/1ps

// -----------------------------------------------------------------------------
// RTL jitter-tolerance harness for cdr_adaptive.
//
// The Python side generates a bit stream with per-boundary time errors (RJ,
// SJ/PJ, DCD, ppm) and writes absolute edge times in picoseconds:
//
//     <time_ps> <bit>
//
// This TB drives data_in at those times, waits for the real CDR to lock, then
// records the recovered bit sequence (one bit per line) plus lock-wait and
// lock-loss metadata.  BER/bit-slip analysis is done in Python.
//
// Plusargs:
//   +STIM=<path>   input edge file (required)
//   +OUT=<path>    recovered-bit file (required)
//   +NBITS=<n>     number of recovered bits to collect (default 4000)
//
// Exit status is 0 even when the CDR does not lock; stdout then contains
// NO_LOCK so the Python runner can report a lock failure instead of a
// simulator error.
// -----------------------------------------------------------------------------
module tb_cdr_jtol;

    parameter int OS       = 8;
    parameter int MAX_BITS = 20000;

    logic clk;
    logic rst_n;
    logic enable;
    logic data_in;
    logic data_out;
    logic sample_strobe;
    logic clk_out;
    logic lock;
    logic lock_stale;
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
    int scan_cycles;

    real half_ns;

    initial begin
        half_ns = 5.0;
        if (!$value$plusargs("HALF_NS=%f", half_ns))
            half_ns = 5.0;
    end

    always #(half_ns) clk = ~clk;

    cdr_adaptive #(
        .OVERSAMPLE(OS),
        .SYNC_STAGES(3),
        .FRAC_BITS(12),
        .LOOP_GAIN(4),
        .LOCK_THRESH(8),
        .LOCK_RELEASE(8),
        .LOCK_STALE_BITS(32),
        .PHASE_DEADBAND(1),
        .LOOP_SETTLE_BITS(32)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .enable(enable),
        .flush(1'b0),
        .data_in(data_in),
        .data_out(data_out),
        .sample_strobe(sample_strobe),
        .clk_out(clk_out),
        .lock(lock),
        .lock_stale(lock_stale),
        .freq_trim(freq_trim)
    );

    always @(posedge clk) begin
        lock_prev <= lock;
        if (lock)
            locked_once <= 1'b1;
        if (locked_once && lock_prev && !lock)
            lock_losses <= lock_losses + 1;
    end

    // Stimulus driver: absolute edge times, quantized to 1 ps.
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

        clk        = 1'b0;
        rst_n      = 1'b0;
        enable     = 1'b0;
        recovered  = 0;
        lock_wait  = 0;
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
        // Collect even if lock later drops: the Python side sees the BER and
        // the lock-loss count, which is the actual JTOL failure criterion.
        scan_cycles = n_target * OS * 2 + 20000;
        for (int cyc = 0; cyc < scan_cycles && recovered < n_target; cyc++) begin
            @(posedge clk);
            #1;
            if (sample_strobe) begin
                rx_bits[recovered] = data_out;
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
