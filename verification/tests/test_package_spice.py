import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from verification.analog.macro_package import stage

ROOT = Path(__file__).resolve().parents[2]
NGSPICE = shutil.which("ngspice")


@unittest.skipUnless(NGSPICE, "ngspice not installed")
class TestPackageSpice(unittest.TestCase):
    def _stage_and_run(self, macro: str, top: str):
        with tempfile.TemporaryDirectory() as td:
            pkg = Path(td) / "pkg"
            out = pkg / "analog"
            pkg.mkdir()
            (pkg / "manifest.json").write_text('{"design":"test"}')
            stage(pkg, out)
            # Copy outside the repository tree before execution.
            run = Path(td) / "standalone"
            shutil.copytree(out, run)
            tb = run / "tb.sp"
            tb.write_text(f""".include "{macro}/canonical.spice"
VDD vdd 0 1.8
VSS vss 0 0
VIN in 0 PULSE(0 1.8 1n 100p 100p 20n 40n)
XDUT vdd vss in out {top}
CLOAD out 0 10f
.tran 10p 60n
.meas tran vhigh FIND v(out) AT=30n
.meas tran vlow FIND v(out) AT=50n
.end
""")
            proc = subprocess.run([NGSPICE, "-b", "tb.sp"], cwd=run,
                                  text=True, capture_output=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stdout[-2000:] + proc.stderr[-2000:])
            text = proc.stdout
            self.assertIn("vhigh", text)
            self.assertIn("vlow", text)
            vhigh = float(text.split("vhigh", 1)[1].split("=", 1)[1].split()[0])
            vlow = float(text.split("vlow", 1)[1].split("=", 1)[1].split()[0])
            self.assertGreater(vhigh, 1.5)
            self.assertLess(vlow, 0.1)

    def test_staged_tx_spice_is_executable_and_inverting(self):
        self._stage_and_run("tx_inv_chain_1_3_9", "tx_inv_chain_1_3_9")

    def test_staged_rx_parallel_spice_is_executable_and_inverting(self):
        self._stage_and_run("rx_parallel_inv_macro", "rx_parallel_inv_macro")

    def test_staged_rx_feedback_spice_is_executable(self):
        with tempfile.TemporaryDirectory() as td:
            pkg = Path(td) / "pkg"; pkg.mkdir(); (pkg / "manifest.json").write_text('{"design":"test"}')
            out = pkg / "analog"; stage(pkg, out)
            run = Path(td) / "standalone"; shutil.copytree(out, run)
            tb = run / "tb.sp"
            tb.write_text(""".include "rx_res_fb_pmos/canonical.spice"
VDD vdd 0 1.8
VSS vss 0 0
VIN in 0 PWL(0 0 1n 0 2n 1.8 60n 1.8)
XDUT vdd vss in out rx_res_fb_pmos
CLOAD out 0 10f
.tran 10p 60n
.meas tran vhigh FIND v(out) AT=40n
.end
""")
            proc = subprocess.run([NGSPICE, "-b", "tb.sp"], cwd=run, text=True,
                                  capture_output=True, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stdout[-1500:] + proc.stderr[-1500:])
            text = proc.stdout
            vhigh = float(text.split("vhigh", 1)[1].split("=", 1)[1].split()[0])
            self.assertLess(vhigh, 0.1)

    def test_identity_manifests_do_not_merge_feedback_and_stdcell(self):
        par = json.loads((ROOT / "analog/macros/rx_parallel_inv_macro/manifest.json").read_text())
        pmos = json.loads((ROOT / "analog/macros/rx_res_fb_pmos/manifest.json").read_text())
        self.assertEqual(par["electrical_topology"], par["physical_topology"])
        self.assertNotIn("feedback", par)
        self.assertTrue(par["physical_signoff_eligible"])
        self.assertTrue(pmos["physical_signoff_eligible"])
        self.assertTrue(pmos["topology_match"])
        self.assertIn("feedback", pmos)
        self.assertEqual(pmos["physical_topology"], "cmos_inverter_with_series_pmos_feedback")
        self.assertNotEqual(par["name"], pmos["name"])


if __name__ == "__main__":
    unittest.main()
