"""CDR_MODE=4 (dll_phase) and the OS=2 source-synchronous endpoint.

Two capabilities added together because they are the two halves of the same
result: `docs/ihp_perf_paths.md` measured that neither drive strength nor design
rules move this design, so the wins come from removing logic (OS=2 fixed
sampling) or keeping width out of the critical adder (the two-stage carry that
makes FREQ_FRAC=10/11 usable, committed separately).

`dll_phase` is the architecture that reaches ~100 MHz.  It emits CDR_MODE=4,
whose sampler lives OUTSIDE the digital netlist: the endpoint exposes
`rx_dll_tap` and consumes `rx_smp`.  The generator therefore has to emit the
two interface parameters (`DLL_PH_W`, `DLL_FREQ_FRAC`) or the package is not
buildable as mode 4.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
GEN = ROOT / "scripts" / "gen_ip.py"


def _base(target: str = "rx") -> dict:
    return {
        "name": "dll", "target": target,
        "link": {"payload_width": 8, "words": 4, "line_rate_mbps": 50,
                 "coding": "raw", "bit_order": "lsb_first"},
        "tx": {"architecture": "parallel_mux"},
        "rx": {"architecture": "dll_phase", "oversample": 4,
               "align": "external", "clocking": "independent", "ppm": 700,
               "sync_len": 8},
        "tech": {"tx": "ihp-sg13g2", "rx": "ihp-sg13g2"},
        "interface": {"preset": "minimal", "data": "valid_ready",
                      "control": "native", "cdc": "none",
                      "enable_mode": "static"},
        "constraints": {"uncertainty_setup_ns": 0.1,
                        "uncertainty_hold_ns": 0.05},
    }


class TestDllPhaseArchitecture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.d = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, spec: dict):
        p = self.d / "s.yaml"
        p.write_text(yaml.safe_dump(spec))
        out = self.d / "o"
        r = subprocess.run([sys.executable, str(GEN), "--spec", str(p),
                            "--out", str(out), "--force"], cwd=ROOT,
                           text=True, capture_output=True, timeout=180)
        return r, out

    def test_dll_phase_emits_cdr_mode_4(self) -> None:
        r, out = self._run(_base())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = json.loads((out / "manifest.json").read_text())
        self.assertEqual(m["params"]["CDR_MODE"], 4)
        # The sampler interface must be in the package or mode 4 cannot build.
        self.assertEqual(m["params"]["DLL_PH_W"], 3)
        self.assertEqual(m["params"]["DLL_FREQ_FRAC"], 8)
        self.assertEqual(m["design"], "serdes_rx_digital")
        self.assertEqual(m["architecture"]["rx"]["name"], "dll_phase")

    def test_dll_phase_knobs_override_from_the_spec(self) -> None:
        spec = _base()
        spec["rx"]["dll_freq_frac"] = 11
        spec["rx"]["dll_ph_w"] = 4
        r, out = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = json.loads((out / "manifest.json").read_text())
        self.assertEqual(m["params"]["DLL_FREQ_FRAC"], 11)
        self.assertEqual(m["params"]["DLL_PH_W"], 4)

    def test_other_modes_do_not_get_the_dll_params(self) -> None:
        # A mode 0-3 package must stay byte-identical to what it was before
        # this architecture existed, so the DLL params are mode-4 only.
        spec = _base()
        spec["rx"] = {"architecture": "fixed_oversampling", "oversample": 4,
                      "align": "external", "clocking": "independent",
                      "ppm": 0, "sync_len": 8}
        r, out = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        m = json.loads((out / "manifest.json").read_text())
        self.assertNotIn("DLL_PH_W", m["params"])
        self.assertNotIn("DLL_FREQ_FRAC", m["params"])

    def test_dll_params_reach_synth_parameters(self) -> None:
        """A param in `params` that is missing from SYNTH_PARAMETERS is dropped.

        That is exactly how the CDR_MODE=4 A/B ran twice with identical
        results: CDR_MODE=4 reached Yosys but DLL_FREQ_FRAC did not, so both
        runs built the same design.  Assert the generated PnR config carries
        every mode-4 interface parameter.
        """
        spec = _base()
        spec["rx"]["dll_freq_frac"] = 11
        spec["rx"]["dll_ph_w"] = 4
        r, out = self._run(spec)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        cfg = yaml.safe_load((out / "pnr" / "librelane.yaml").read_text())
        synth = cfg["SYNTH_PARAMETERS"]
        self.assertIn("DLL_FREQ_FRAC=11", synth)
        self.assertIn("DLL_PH_W=4", synth)
        self.assertIn("CDR_MODE=4", synth)

    def test_dll_phase_accepts_a_ppm_offset(self) -> None:
        # Unlike a fixed-phase sampler, mode 4 has a real tracking loop, so a
        # non-zero ppm must NOT be rejected.
        r, _ = self._run(_base())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestSourceSyncOs2Spec(unittest.TestCase):
    """The shipped recommendation in spec/rx_source_sync_os2.yaml."""

    def test_shipped_spec_generates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "o"
            r = subprocess.run(
                [sys.executable, str(GEN), "--spec",
                 str(ROOT / "spec" / "rx_source_sync_os2.yaml"),
                 "--out", str(out), "--force"],
                cwd=ROOT, text=True, capture_output=True, timeout=180)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            m = json.loads((out / "manifest.json").read_text())
            self.assertEqual(m["params"]["OVERSAMPLE"], 2)
            self.assertEqual(m["params"]["CDR_MODE"], 0)
            self.assertEqual(m["architecture"]["rx"]["name"],
                             "fixed_oversampling_os2")

    def test_os2_is_rejected_without_allow_os2(self) -> None:
        # The opt-in exists because OS=2 has no jitter margin; it must not be
        # reachable by just setting oversample: 2.
        spec = _base()
        spec["rx"] = {"architecture": "fixed_oversampling", "oversample": 2,
                      "align": "external", "clocking": "independent",
                      "ppm": 0, "sync_len": 8}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "s.yaml"
            p.write_text(yaml.safe_dump(spec))
            r = subprocess.run(
                [sys.executable, str(GEN), "--spec", str(p),
                 "--out", str(Path(tmp) / "o"), "--force"],
                cwd=ROOT, text=True, capture_output=True, timeout=180)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("allow_os2", r.stdout + r.stderr)

    def test_os2_fixed_rejects_a_ppm_offset(self) -> None:
        # A fixed phase cannot track an offset.  The shipped spec documents
        # ppm: 0 as mandatory and the generator must enforce it.
        spec = _base()
        spec["rx"] = {"architecture": "fixed_oversampling_os2",
                      "oversample": 2, "allow_os2": True,
                      "align": "external", "clocking": "independent",
                      "ppm": 700, "sync_len": 8}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "s.yaml"
            p.write_text(yaml.safe_dump(spec))
            r = subprocess.run(
                [sys.executable, str(GEN), "--spec", str(p),
                 "--out", str(Path(tmp) / "o"), "--force"],
                cwd=ROOT, text=True, capture_output=True, timeout=180)
            self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
