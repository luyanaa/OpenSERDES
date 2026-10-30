#!/usr/bin/env python3
"""GF180MCU RX macro post-PEX electrical characterization.

The existing macro verifier stops at Magic DRC/LVS/PEX extraction plus a
model-binding check.  This script starts from the extracted PEX netlist and
runs the same independent DC/AC/transient/noise testbench used for the IHP
repeatability audit.  It also builds a known 1 um x 100 um MET1 line, asks
Magic ``extresist`` to report its wire resistance, and records whether the
resistance is actually emitted into the SPICE netlist.

This is macro-level characterization, not full-chip or foundry signoff.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.gf180_rx_macro import (  # noqa: E402
    CELL,
    _find_gf_root,
    _gf_paths,
    _env,
    _run,
    _sha256,
)

DEFAULT_PEX = ROOT / "build/verify/gf180_rx_macro/gf180_rx_macro.raw.spice"
DEFAULT_GDS = ROOT / "build/verify/gf180_rx_macro/gf180_rx_macro.gds"
DEFAULT_OUT = ROOT / "build/verify/gf180_rx_macro/postpex.json"
M1_LAYER = (34, 0)
M1_LABEL_LAYER = (34, 10)
M1_SHEET_OHM_PER_SQ = 0.090


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_body(path: Path) -> str:
    lines: list[str] = []
    for line in path.read_text(errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("*"):
            continue
        if s.lower().startswith(".option scale"):
            continue
        if s.lower().startswith(".end"):
            continue
        s = s.split("**", 1)[0].rstrip()
        if s:
            lines.append(s)
    return "\n".join(lines)


def _deck(pdk: Path, pex: Path, work: Path) -> tuple[Path, Path]:
    body = _clean_body(pex)
    common = "\n".join([
        "* gf180mcuD RX macro post-PEX electrical characterization",
        f'.include "{pdk / "libs.tech/ngspice/design.ngspice"}"',
        f'.lib "{pdk / "libs.tech/ngspice/sm141064.spice"}" typical',
        ".option scale=5n",
        f".subckt {CELL} VSS VDD OUT IN",
        body,
        ".ends",
        "VDD VDD 0 3.3",
        "VSS VSS 0 0",
        "VIN IN 0 DC 1.65 AC 1 PULSE(1.65 2.5 1n 100p 100p 2n 5n)",
        f"XDUT VSS VDD OUT IN {CELL}",
    ])
    sim = work / "postpex_dcac.sp"
    sim.write_text(
        common
        + "\n.control\n"
        "set filetype=ascii\n"
        "op\n"
        "print v(out) i(vdd) i(vin)\n"
        "dc VIN 0 3.3 0.1\n"
        f"wrdata {work / 'dc.dat'} v(out)\n"
        "ac dec 20 1 1g\n"
        f"wrdata {work / 'ac.dat'} v(out)\n"
        f"wrdata {work / 'ac_i.dat'} i(vin)\n"
        "tran 100p 4n 0 50p\n"
        f"wrdata {work / 'tran.dat'} v(in) v(out)\n"
        "noise v(out) vin dec 10 1 1g\n"
        "print onoise_total\n"
        ".endc\n.end\n"
    )
    # Separate noise deck is not needed for GF180 because the OSDI warning
    # path used by IHP is absent, but keep it separate for a clean failure.
    return sim, work / "postpex_dcac.sp"


def _parse_op(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for name, val in re.findall(
            r"^(v\([^)]+\)|i\([^)]+\))\s*=\s*([-+0-9.eE]+)", text, re.M):
        try:
            values[name] = float(val)
        except ValueError:
            pass
    return values


def _arr(path: Path, cols: int) -> np.ndarray:
    if not path.is_file():
        raise FileNotFoundError(path)
    a = np.loadtxt(path)
    if a.ndim == 1:
        a = a.reshape(1, -1)
    if a.shape[1] < cols:
        raise ValueError(f"{path}: got {a.shape}, expected >= {cols} columns")
    return a


def _metrics(pdk: Path, pex: Path, work: Path, env: dict[str, str],
             ngspice: str) -> dict:
    for stale in ("dc.dat", "ac.dat", "ac_i.dat", "tran.dat"):
        (work / stale).unlink(missing_ok=True)
    deck, _ = _deck(pdk, pex, work)
    rep = _run([ngspice, "-n", "-b", str(deck)], cwd=work, env=env,
               log_path=work / "postpex_sim.log", timeout=900)
    text = (work / "postpex_sim.log").read_text(errors="replace")
    out: dict = {
        "returncode": rep["returncode"],
        "command": rep["command"],
        "op": _parse_op(text),
        "errors": [ln.strip() for ln in text.splitlines()
                   if "error" in ln.lower() and "warning" not in ln.lower()],
    }
    dc = _arr(work / "dc.dat", 2)
    ac = _arr(work / "ac.dat", 3)
    aci = _arr(work / "ac_i.dat", 3)
    tr = _arr(work / "tran.dat", 3)
    f = ac[:, 0]
    vout = ac[:, 1] + 1j * ac[:, 2]
    iin = aci[:, 1] + 1j * aci[:, 2]

    def at(target: float) -> dict:
        i = int(np.argmin(np.abs(f - target)))
        gain = 20 * np.log10(max(abs(vout[i]), 1e-30))
        cap = abs(iin[i]) / (2 * np.pi * f[i]) if f[i] else None
        return {"freq_hz": float(f[i]), "gain_db": float(gain),
                "phase_deg": float(np.degrees(np.angle(vout[i]))),
                "input_cap_f": float(cap) if cap is not None else None}

    # DC sweep: wrdata writes the swept variable and v(out), i.e. (vin, vout).
    if dc.shape[1] >= 3:
        x, y = dc[:, 1], dc[:, 2]
    else:
        x, y = dc[:, 0], dc[:, 1]
    mid = int(np.argmin(np.abs(y - 1.65)))
    out["dc_sweep"] = {
        "points": int(len(y)),
        "vout_min": float(y.min()),
        "vout_max": float(y.max()),
        "trip_point_index": mid,
        "trip_input_v": float(x[mid]),
        "mid_vout_v": float(y[mid]),
    }
    out["ac"] = {"low": at(1e3), "mid": at(1e6),
                 "high": at(1e8), "input_cap_1m_f": at(1e6)["input_cap_f"]}
    # ngspice wrdata repeats the scale column when more than one vector is
    # requested: (time, v(in), time, v(out)).  Normalize both layouts.
    if tr.shape[1] >= 4:
        t, vin_t, vout_t = tr[:, 0], tr[:, 1], tr[:, 3]
    else:
        t, vin_t, vout_t = tr[:, 0], tr[:, 1], tr[:, 2]
    out["tran"] = {
        "points": int(len(t)),
        "vout_min": float(vout_t.min()),
        "vout_max": float(vout_t.max()),
        "vout_final": float(vout_t[-1]),
        "vin_final": float(vin_t[-1]),
    }
    m = re.search(r"onoise_total\s*=\s*([-+0-9.eE]+)", text)
    out["noise"] = {"onoise_total_v": float(m.group(1)) if m else None}
    return out


def _make_known_resistor_gds(gds: Path, work: Path, klayout: str,
                             env: dict[str, str]) -> dict:
    script = work / "make_known_resistor.py"
    script.write_text(f"""import pya
ly = pya.Layout(); ly.dbu = 0.005
c = ly.create_cell('known_m1_resistor')
m1 = ly.layer({M1_LAYER[0]}, {M1_LAYER[1]})
lab = ly.layer({M1_LABEL_LAYER[0]}, {M1_LABEL_LAYER[1]})
c.shapes(m1).insert(pya.Box(0, 0, 20000, 200))
c.shapes(lab).insert(pya.Text('IN', pya.Trans(0, 100)))
c.shapes(lab).insert(pya.Text('OUT', pya.Trans(20000, 100)))
ly.write('{gds}')
print('KNOWN_RESISTOR_GDS_OK')
""")
    return _run([klayout, "-b", "-r", str(script)], cwd=work, env=env,
                log_path=work / "make_known_resistor.log", timeout=300)


def _calibrate(gf_root: Path, pdk: Path, env: dict[str, str],
               work: Path, magic: str, klayout: str) -> dict:
    gds = work / "known_m1_resistor.gds"
    gen = _make_known_resistor_gds(gds, work, klayout, env)
    tcl = work / "known_resistor_pex.tcl"
    raw = work / "known_m1_resistor.raw.spice"
    tcl.write_text("\n".join([
        f"gds read {{{gds.with_suffix('')}}}",
        "load known_m1_resistor -dereference",
        "select top cell",
        "port makeall",
        "extract",
        "ext2sim labels on",
        "ext2sim",
        "extresist debug on",
        "extresist threshold 0",
        "extresist minresist 0",
        "extresist mindelay 0",
        "extresist all",
        "ext2spice cthresh 0",
        "ext2spice rthresh 0",
        "ext2spice extresist on",
        f"ext2spice -o {{{raw}}}",
        'puts "KNOWN_RESISTOR_PEX_DONE"',
        "quit",
    ]))
    rep = _run([magic, "-dnull", "-noconsole", "-rcfile",
                str(pdk / "libs.tech/magic/gf180mcuD.magicrc"), str(tcl)],
               cwd=work, env=env, log_path=work / "known_resistor_pex.log",
               timeout=600)
    log = (work / "known_resistor_pex.log").read_text(errors="replace")
    rnodes = [float(x) for x in re.findall(r"Rnode\s*=\s*([0-9.]+)ohm", log)]
    rnode = max(rnodes) if rnodes else None
    raw_text = raw.read_text(errors="replace") if raw.is_file() else ""
    emitted_r = []
    for line in raw_text.splitlines():
        s = line.strip()
        if s.startswith("R") and not s.startswith("*"):
            tok = s.split()
            if len(tok) >= 4:
                try:
                    emitted_r.append(float(tok[3]))
                except ValueError:
                    pass
    expected = M1_SHEET_OHM_PER_SQ * (100.0 / 1.0)
    return {
        "structure": "1 um wide x 100 um long MET1 line, two labels/ports",
        "expected_r_ohm": expected,
        "magic_rnode_ohm": rnode,
        "emitted_spice_resistors_ohm": emitted_r,
        "emitted_spice_total_ohm": sum(emitted_r),
        "rnode_matches_sheet_resistance": bool(
            rnode is not None and abs(rnode - expected) / expected <= 0.10),
        "metal_line_r_emitted": bool(sum(emitted_r) > 0.1 * expected),
        "extraction_log": str(work / "known_resistor_pex.log"),
        "raw_spice": str(raw),
        "returncode": rep["returncode"],
        "note": (
            "With 'ext2sim' before 'extresist' and 'extresist mindelay 0', "
            "Magic emits the full wire resistance (R0 IN OUT.n0 ~= 9.0 ohm) "
            "for the bare MET1 line.  Without 'mindelay 0' extresist drops "
            "every net whose RC delay is below its default Tmin floor "
            "('Not adding ...'), yielding Nets output: 0 and R=0.  That was "
            "the previous failure mode, not an absence of a wire-R model."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gf-root", default=None)
    ap.add_argument("--pex", default=str(DEFAULT_PEX))
    ap.add_argument("--gds", default=str(DEFAULT_GDS))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--workdir", default=None)
    args = ap.parse_args()
    gf_root = _find_gf_root(args.gf_root)
    paths = _gf_paths(gf_root)
    env = _env(gf_root)
    pdk = paths["pdk"]
    pex = Path(args.pex).resolve()
    if not pex.is_file():
        raise SystemExit(f"missing PEX netlist: {pex}")
    work = Path(args.workdir).resolve() if args.workdir else ROOT / "build/verify/gf180_rx_macro/postpex"
    work.mkdir(parents=True, exist_ok=True)
    magic = Path(__import__("shutil").which("magic") or "magic")
    klayout = Path(__import__("shutil").which("klayout") or "klayout")
    ngspice = Path(__import__("shutil").which("ngspice") or "ngspice")
    sim = _metrics(pdk, pex, work, env, str(ngspice))
    cal = _calibrate(gf_root, pdk, env, work, str(magic), str(klayout))
    # Count the PEX elements from the same file used by the simulation.
    counts = {"X": 0, "R": 0, "C": 0}
    for line in pex.read_text(errors="replace").splitlines():
        s = line.strip()
        if s.startswith(("X", "R", "C")):
            counts[s[0]] += 1
    report = {
        "kind": "gf180-rx-macro-post-pex-analog",
        "generated_utc": _utc(),
        "pdk": str(pdk),
        "pex": str(pex),
        "pex_sha256": _sha256(pex),
        "gds": str(Path(args.gds).resolve()),
        "pex_counts": counts,
        "simulation": sim,
        "known_resistor_calibration": cal,
        "checks": {
            "dc_op_converged": bool(sim.get("op")) and sim.get("returncode") == 0,
            "ac_completed": bool(sim.get("ac", {}).get("mid", {}).get("gain_db") is not None),
            "transient_completed": bool(sim.get("tran", {}).get("points", 0) > 10),
            "noise_completed": sim.get("noise", {}).get("onoise_total_v") is not None,
            "known_rnode_matches_geometry": cal["rnode_matches_sheet_resistance"],
            "metal_line_r_emitted": cal["metal_line_r_emitted"],
        },
        "status": None,
        "scope": (
            "Macro-level post-PEX DC/AC/transient/noise characterization for "
            "one GF180MCU RX placement. Not full-chip/foundry signoff. "
            "The known-resistor calibration distinguishes extresist's internal "
            "Rnode calculation from the R actually emitted in SPICE."
        ),
    }
    report["status"] = "POST_PEX_ANALOG_CHARACTERIZED" if all(
        report["checks"][k] for k in (
            "dc_op_converged", "ac_completed", "transient_completed",
            "noise_completed", "known_rnode_matches_geometry",
            "metal_line_r_emitted")) else "POST_PEX_ANALOG_PARTIAL"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({
        "status": report["status"],
        "op": sim.get("op"),
        "ac_mid_db": sim.get("ac", {}).get("mid", {}).get("gain_db"),
        "input_cap_1m_f": sim.get("ac", {}).get("input_cap_1m_f"),
        "tran": sim.get("tran"),
        "noise": sim.get("noise"),
        "calibration": {k: cal[k] for k in (
            "expected_r_ohm", "magic_rnode_ohm", "emitted_spice_total_ohm",
            "rnode_matches_sheet_resistance", "metal_line_r_emitted")},
        "checks": report["checks"],
    }, indent=2))
    return 0 if report["status"] == "POST_PEX_ANALOG_CHARACTERIZED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
