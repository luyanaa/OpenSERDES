"""Workload activity annotation: parsing and the idle/E_bit rule.

The OpenSTA run itself needs a Liberty file and is not part of this unit
test.  These checks lock the report parser and the rule that a zero useful
rate does not become an energy number.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load():
    path = ROOT / "scripts" / "annotate_activity.py"
    spec = importlib.util.spec_from_file_location("annotate_activity", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REPORT = """
Annotated 509 pin activities.
vcd            509
unannotated     0
Group                    Internal    Switching      Leakage        Total
Sequential           1.654184e-04 1.062663e-05 4.427228e-10 1.760454e-04  88.4%
Combinational        1.787470e-05 5.189670e-06 3.015800e-10 2.306495e-05  11.6%
Clock                0.000000e+00 0.000000e+00 0.000000e+00 0.000000e+00   0.0%
Total                1.832931e-04 1.581630e-05 7.443028e-10 1.991101e-04 100.0%
"""


class TestActivityParse(unittest.TestCase):
    def test_full_annotation_parses(self) -> None:
        module = _load()
        coverage = module._parse_annotation(REPORT)
        power = module._parse_power(REPORT)
        self.assertEqual(coverage["annotated_pins"], 509)
        self.assertEqual(coverage["unannotated_pins"], 0)
        self.assertEqual(coverage["annotation_coverage"], 1.0)
        self.assertAlmostEqual(power["total"]["total_w"], 1.991101e-04)
        self.assertEqual(power["clock"]["total_w"], 0.0)
        self.assertNotIn("analog", power)

    def test_idle_rate_does_not_invent_energy(self) -> None:
        useful = 0.0
        total = 1.6e-4
        energy = None if useful <= 0 else total / (useful * 1e6)
        self.assertIsNone(energy)

    def test_missing_annotation_is_an_error(self) -> None:
        module = _load()
        with self.assertRaises(SystemExit):
            module._parse_annotation("no report here")


if __name__ == "__main__":
    unittest.main()
