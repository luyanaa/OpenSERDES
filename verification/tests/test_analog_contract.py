import copy
import json
import unittest
from pathlib import Path

from verification.analog.macro_contract import (
    DEFAULT_MACROS,
    canonical_ports,
    load_manifest,
    validate_manifest,
    view_ports,
    alias_map,
)

ROOT = Path(__file__).resolve().parents[2]


class TestAnalogContract(unittest.TestCase):
    def setUp(self) -> None:
        self.tx_path = DEFAULT_MACROS / "tx_inv_chain_1_3_9" / "manifest.json"
        self.rx_path = DEFAULT_MACROS / "rx_res_fb_inv" / "manifest.json"
        self.tx = load_manifest(self.tx_path)
        self.rx = load_manifest(self.rx_path)

    def test_both_contracts_validate(self) -> None:
        for path, manifest in ((self.tx_path, self.tx), (self.rx_path, self.rx)):
            errors, warnings = validate_manifest(path, manifest)
            self.assertEqual(errors, [], path)
            self.assertEqual(warnings, [], path)
            self.assertEqual(canonical_ports(manifest), ["VDD", "VSS", "IN", "OUT"])

    def test_rx_historical_orders_map_to_same_canonical_set(self) -> None:
        ports = canonical_ports(self.rx)
        aliases = alias_map(self.rx, ports)
        sch = view_ports(self.rx, "schematic", ports, aliases)
        lay = view_ports(self.rx, "layout_spice", ports, aliases)
        self.assertNotEqual(sch, lay)
        self.assertEqual(sorted(sch), ["IN", "OUT", "VDD", "VSS"])
        self.assertEqual(sorted(lay), ["IN", "OUT", "VDD", "VSS"])
        self.assertEqual(sch, ["VSS", "IN", "OUT", "VDD"])
        self.assertEqual(lay, ["VSS", "VDD", "IN", "OUT"])

    def test_canonical_wrappers_map_by_name(self) -> None:
        for manifest in (self.tx, self.rx):
            wrapper = ROOT / manifest["views"]["canonical_spice"]["path"]
            text = wrapper.read_text()
            self.assertIn("Xcore VSS VDD IN OUT", text)
            self.assertIn("VDD VSS IN OUT", text)
            self.assertEqual(manifest["polarity"], "inverting")
            self.assertTrue(manifest["polarity_verified"])

    def test_missing_alias_is_rejected(self) -> None:
        bad = copy.deepcopy(self.rx)
        bad["aliases"]["IN"] = ["IN"]  # removes INP used by both RX views
        errors, _ = validate_manifest(self.rx_path, bad)
        self.assertTrue(errors)

    def test_duplicate_or_unknown_view_port_is_rejected(self) -> None:
        bad = copy.deepcopy(self.rx)
        bad["views"]["schematic"]["port_order"] = ["GND", "GND", "OUT", "VDD"]
        errors, _ = validate_manifest(self.rx_path, bad)
        self.assertTrue(errors)
        bad = copy.deepcopy(self.rx)
        bad["views"]["layout_spice"]["port_order"] = ["GND", "VDD", "NOPE", "OUT"]
        errors, _ = validate_manifest(self.rx_path, bad)
        self.assertTrue(errors)

    def test_calibre_rve_cannot_be_available(self) -> None:
        bad = copy.deepcopy(self.rx)
        bad["views"]["pex"]["status"] = "available"
        errors, _ = validate_manifest(self.rx_path, bad)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
