#!/usr/bin/env python3
"""IHP RX macro PEX repeatability and port-response audit.

This does not compare only component counts.  It runs the same GDS through
five isolated Magic ``extract/extresist`` processes, then drives each extracted
netlist with the same DC/AC/transient/noise testbench and compares the
port-level responses.  Structural R/C differences are acceptable only if the
electrical responses agree within the configured tolerance; otherwise the run
is reported as a real repeatability failure.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verification.analog.ihp_device_verify import ensure_osdi, resolve_pdk  # noqa: E402
from verification.analog.ihp_rx_macro import (  # noqa: E402
    DEFAULT_IHP_ROOT,
    MACRO,
    magic_pex,
    parse_pex,
)
from verification.analog.ihp_device_verify import run  # noqa: E402

DEFAULT_GDS = ROOT / "build/verify/ihp_rx_macro/rx_macro.gds"
DEFAULT_LIB = "libs.tech/ngspice/models/cornerMOSlv.lib"


def _clean_body(pex: Path) -> str:
    lines = []
    for line in pex.read_text().splitlines():
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


def _netlist(pdk: Path, pex: Path, work: Path) -> Path:
    lib = pdk / DEFAULT_LIB
    body = _clean_body(pex)
    common = (
        f".lib {lib} mos_tt\n"
        ".subckt rx_macro VSS VDD IN OUT\n"
        f"{body}\n"
        ".ends\n"
        "VDD VDD 0 1.2\n"
        "VSS VSS 0 0\n"
        "VIN IN 0 DC 0.6 AC 1 PULSE(0.6 1.2 1n 100p 100p 4n 10n)\n"
        "Xdut VSS VDD IN OUT rx_macro\n"
    )
    work.joinpath("op_ac_tran.sp").write_text(
        common
        + ".control\n"
        "set filetype=ascii\n"
        "op\n"
        "print v(out) i(vdd) i(vin)\n"
        "ac dec 20 1 1g\n"
        f"wrdata {work / 'ac.dat'} v(out)\n"
        f"wrdata {work / 'ac_i.dat'} i(vin)\n"
        "tran 10p 20n\n"
        f"wrdata {work / 'tran.dat'} v(in) v(out)\n"
        ".endc\n.end\n"
    )
    work.joinpath("noise.sp").write_text(
        common
        + ".control\n"
        "set filetype=ascii\n"
        "noise v(out) vin dec 20 1 1g\n"
        "print onoise_total\n"
        ".endc\n.end\n"
    )
    return work / "op_ac_tran.sp"


def _parse_op(text: str) -> dict[str, float]:
    out = {}
    for name, val in re.findall(
            r"^(v\([^)]+\)|i\([^)]+\))\s*=\s*([-+0-9.eE]+)", text, re.M):
        try:
            out[name] = float(val)
        except ValueError:
            pass
    return out


def _read_data(path: Path, ncols: int) -> np.ndarray:
    if not path.is_file():
        raise FileNotFoundError(path)
    arr = np.loadtxt(path)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.shape[1] < ncols:
        raise ValueError(f"{path}: expected >= {ncols} columns, got {arr.shape}")
    return arr


def _metrics(pdk: Path, pex: Path, work: Path, env: dict[str, str]) -> dict:
    for stale in ("ac.dat", "ac_i.dat", "tran.dat"):
        (work / stale).unlink(missing_ok=True)
    net = _netlist(pdk, pex, work)
    (work / "logs").mkdir(exist_ok=True)
    log = work / "logs/sim.log"
    proc = run(["ngspice", "-b", str(net)], work, env, log, timeout=900)
    text = log.read_text(errors="replace")
    metrics: dict[str, object] = {
        "returncode": proc["returncode"],
        "op": _parse_op(text),
        "errors": [ln.strip() for ln in text.splitlines()
                   if "error" in ln.lower() and "osdi" not in ln.lower()],
    }
    ac = _read_data(work / "ac.dat", 3)
    f = ac[:, 0]
    vout = ac[:, 1] + 1j * ac[:, 2]
    vin = np.ones_like(f, dtype=complex)  # source is 1 V AC
    aci = _read_data(work / "ac_i.dat", 3)
    iin = aci[:, 1] + 1j * aci[:, 2]
    gain = vout / np.where(np.abs(vin) > 0, vin, 1.0)
    def at(target):
        i = int(np.argmin(np.abs(f - target)))
        return {"freq_hz": float(f[i]),
                "gain_db": float(20 * np.log10(max(abs(gain[i]), 1e-30))),
                "phase_deg": float(np.degrees(np.angle(gain[i])))}
    metrics["ac"] = {
        "low": at(1e3),
        "mid": at(1e6),
        "high": at(1e8),
        "input_cap_1m_f": float(abs(iin[int(np.argmin(np.abs(f - 1e6)))]) /
                               (2 * np.pi * f[int(np.argmin(np.abs(f - 1e6)))])),
    }
    tr = _read_data(work / "tran.dat", 3)
    # ngspice wrdata repeats the scale column for multiple vectors:
    # (time, v(in), time, v(out)).
    if tr.shape[1] >= 4:
        t, vin_t, vout_t = tr[:, 0], tr[:, 1], tr[:, 3]
    else:
        t, vin_t, vout_t = tr[:, 0], tr[:, 1], tr[:, 2]
    probes = {}
    for target_ns in (0.0, 1.0, 1.5, 2.0, 3.0, 5.0, 10.0, 20.0):
        idx = int(np.argmin(np.abs(t - target_ns * 1e-9)))
        probes[f"{target_ns:g}ns"] = float(vout_t[idx])
    metrics["tran"] = {
        "vout_min": float(vout_t.min()),
        "vout_max": float(vout_t.max()),
        "vout_final": float(vout_t[-1]),
        "vin_final": float(vin_t[-1]),
        "samples": int(len(t)),
        "vout_at": probes,
    }
    # Noise is optional: the OSDI setup can emit non-fatal missing-cap warnings.
    noise = work / "noise.log"
    nres = run(["ngspice", "-b", str(work / "noise.sp")], work, env, noise, timeout=900)
    ntext = noise.read_text(errors="replace")
    m = re.search(r"onoise_total\s*=\s*([-+0-9.eE]+)", ntext)
    metrics["noise"] = {
        "returncode": nres["returncode"],
        "onoise_total_v": float(m.group(1)) if m else None,
    }
    return metrics


def _spread(values: list[float]) -> float:
    vals = [v for v in values if v is not None and np.isfinite(v)]
    if len(vals) < 2:
        return 0.0
    lo, hi = min(vals), max(vals)
    denom = max(abs((lo + hi) / 2.0), 1e-30)
    return (hi - lo) / denom


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gds", default=str(DEFAULT_GDS))
    ap.add_argument("--ihp-root", default=str(DEFAULT_IHP_ROOT))
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", default="build/verify/ihp_pex_repeatability")
    args = ap.parse_args()
    gds = Path(args.gds).resolve()
    if not gds.is_file():
        raise SystemExit(f"missing GDS: {gds}")
    pdk = resolve_pdk(Path(args.ihp_root).expanduser().resolve())
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    env = dict(__import__("os").environ)
    env["IHP_ROOT"] = str(pdk.parent)
    env["PDK_ROOT"] = str(pdk.parent)
    env["PDK"] = "ihp-sg13g2"
    env["KLAYOUT_PATH"] = str(pdk / "libs.tech/klayout")
    osdi = ensure_osdi(pdk, False, out, env)

    runs = []
    for i in range(args.runs):
        work = out / f"run_{i + 1:02d}"
        work.mkdir(parents=True, exist_ok=True)
        for stale in list(work.glob("*.ext")) + list(work.glob("*.res.ext")):
            stale.unlink(missing_ok=True)
        pex = magic_pex(pdk, gds, work, env, f"run_{i + 1:02d}")
        if not pex.get("netlist"):
            runs.append({"run": i + 1, "extraction": pex, "error": "no PEX netlist"})
            continue
        p = Path(pex["netlist"])
        parsed = parse_pex(p)
        try:
            sim = _metrics(pdk, p, work, env)
        except Exception as exc:  # noqa: BLE001
            sim = {"error": str(exc)[-1200:]}
        runs.append({"run": i + 1, "extraction": pex, "pex": parsed, "sim": sim})
        print(f"run {i + 1}: X={parsed['device_instances']} R={parsed['resistors']} "
              f"C={parsed['capacitors']} sim={sim.get('returncode', 'ERROR')}",
              flush=True)
        out.joinpath("repeatability.json").write_text(
            json.dumps({"runs": runs}, indent=2) + "\n")

    # Compare only runs with a complete simulation.
    full = [r for r in runs if isinstance(r.get("sim"), dict)
            and "op" in r["sim"] and "ac" in r["sim"] and "tran" in r["sim"]]
    structural = {
        "device_instances": sorted({r["pex"]["device_instances"] for r in full}) if full else [],
        "resistors": [r["pex"]["resistors"] for r in full],
        "capacitors": [r["pex"]["capacitors"] for r in full],
        "floating_caps": [r["pex"]["floating_caps"] for r in full],
        "r_count_stable": len({r["pex"]["resistors"] for r in full}) <= 1 if full else False,
        "c_count_stable": len({r["pex"]["capacitors"] for r in full}) <= 1 if full else False,
    }
    electrical = {}
    if full:
        electrical = {
            "op_out_v": [r["sim"]["op"].get("v(out)") for r in full],
            "op_i_vdd": [r["sim"]["op"].get("i(vdd)") for r in full],
            "ac_low_db": [r["sim"]["ac"]["low"]["gain_db"] for r in full],
            "ac_mid_db": [r["sim"]["ac"]["mid"]["gain_db"] for r in full],
            "ac_high_db": [r["sim"]["ac"]["high"]["gain_db"] for r in full],
            "input_cap_1m_f": [r["sim"]["ac"]["input_cap_1m_f"] for r in full],
            "tran_vout_final": [r["sim"]["tran"]["vout_final"] for r in full],
            "tran_vout_min": [r["sim"]["tran"]["vout_min"] for r in full],
            "tran_vout_max": [r["sim"]["tran"]["vout_max"] for r in full],
            "noise_total_v": [r["sim"]["noise"].get("onoise_total_v") for r in full],
        }
    spreads = {k: _spread([float(x) for x in v if x is not None])
               for k, v in electrical.items() if isinstance(v, list)}
    # Fixed-time transient probes make the "same stimulus" comparison explicit
    # rather than relying only on final values.
    tran_probe_spreads = {}
    if full:
        for key in full[0]["sim"]["tran"].get("vout_at", {}):
            vals = [r["sim"]["tran"]["vout_at"].get(key) for r in full]
            tran_probe_spreads[f"tran_{key}"] = _spread([float(x) for x in vals if x is not None])
        spreads.update(tran_probe_spreads)
    # This is a diagnostic gate: the user can tighten it after choosing a spec.
    electrical_stable = bool(spreads) and all(v <= 0.05 for v in spreads.values())
    report = {
        "kind": "ihp-sg13g2-pex-repeatability",
        "gds": str(gds),
        "pdk": str(pdk),
        "runs_requested": args.runs,
        "runs_complete": len(full),
        "osdi": osdi,
        "structural": structural,
        "electrical": electrical,
        "relative_spread": spreads,
        "electrical_stable_at_5pct": electrical_stable,
        "classification": (
            "electrically-equivalent-with-different-rc-segmentation"
            if electrical_stable and not structural["r_count_stable"]
            else "repeatable" if electrical_stable
            else "not-electrically-repeatable"
        ),
        "runs": runs,
        "note": ("Structural R/C count differences are not automatically a "
                 "failure; the gate is the port-level response comparison. "
                 "The 5% threshold is a diagnostic default, not a PDK spec."),
    }
    out.joinpath("repeatability.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in (
        "runs_complete", "structural", "relative_spread",
        "electrical_stable_at_5pct", "classification")}, indent=2))
    return 0 if len(full) == args.runs and electrical_stable else 1


if __name__ == "__main__":
    raise SystemExit(main())
