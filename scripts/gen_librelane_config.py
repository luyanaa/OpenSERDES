#!/usr/bin/env python3
"""Generate a LibreLane config for one abstract block and one tech profile."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import yaml

DESIGNS = {
    "serializer": ["rtl/serializer.sv"],
    "deserializer": ["rtl/deserializer_word.sv", "rtl/deserializer.sv"],
    "serdes_tx_digital": [
        "rtl/serializer.sv",
        "rtl/reset_sync.sv",
        "rtl/serdes_tx_digital.sv",
    ],
    "serdes_tx_stream_digital": [
        "rtl/serializer_stream.sv",
        "rtl/reset_sync.sv",
        "rtl/serdes_tx_stream_digital.sv",
    ],
    "serdes_rx_digital": [
        "rtl/deserializer_word.sv",
        "rtl/deserializer.sv",
        "rtl/cdr_sync.sv",
        "rtl/cdr_oversampling.sv",
        "rtl/cdr_adaptive.sv",
        "rtl/comma_align.sv",
        "rtl/receiver_sampler.sv",
        "rtl/reset_sync.sv",
        "rtl/serdes_rx_digital.sv",
    ],
    "serdes_digital_top": [
        "rtl/serializer.sv",
        "rtl/deserializer_word.sv",
        "rtl/deserializer.sv",
        "rtl/cdr_sync.sv",
        "rtl/cdr_oversampling.sv",
        "rtl/cdr_adaptive.sv",
        "rtl/comma_align.sv",
        "rtl/receiver_sampler.sv",
        "rtl/reset_sync.sv",
        "rtl/serdes_tx_digital.sv",
        "rtl/serdes_rx_digital.sv",
        "rtl/serdes_digital_top.sv",
    ],
}


def _expand_tree(value):
    """Expand $VAR/${VAR} and ~ in every string of the loaded tech profile."""
    if isinstance(value, str):
        expanded = os.path.expanduser(os.path.expandvars(value))
        if "${" in expanded or "$(" in expanded:
            raise SystemExit(
                "unresolved environment variable in tech profile: "
                f"{value!r}\n"
                "set PDK_ROOT and PDK_LIB_ROOT (or use --pdk-root/--pdk-lib-root)"
            )
        return expanded
    if isinstance(value, list):
        return [_expand_tree(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_tree(v) for k, v in value.items()}
    return value


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tech", required=True, help="tech profile directory name")
    ap.add_argument("--design", required=True, choices=sorted(DESIGNS))
    ap.add_argument("--period", type=float, default=None)
    ap.add_argument("--scl", default=None, help="override standard-cell library")
    ap.add_argument("--capture-mode", action="store_true", help="serializer word-group capture")
    ap.add_argument("--oversample", type=int, default=None, help="top-level CDR oversampling factor")
    ap.add_argument("--cdr-bypass", action="store_true", help="receiver-only mode: external rx_clk is the bit clock")
    ap.add_argument("--src-root", default=None, help="root for VERILOG_FILES; defaults to this repository")
    ap.add_argument("--pdk-root", default=None, help="PDK_ROOT used for LibreLane; overrides the environment")
    ap.add_argument("--pdk-lib-root", default=None, help="PDK_LIB_ROOT containing the liberty tree; defaults to PDK_ROOT")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    here = Path(__file__).resolve().parent.parent

    # The tech profiles use ${PDK_ROOT} and ${PDK_LIB_ROOT}.  PDK_ROOT is the
    # directory passed to LibreLane; PDK_LIB_ROOT is the directory containing
    # the PDK's libs.ref tree (they are the same for sky130/gf180, but not for
    # the IHP and icsprout55 overlays).
    if args.pdk_root:
        os.environ["PDK_ROOT"] = args.pdk_root
    if args.pdk_lib_root:
        os.environ["PDK_LIB_ROOT"] = args.pdk_lib_root
    elif not os.environ.get("PDK_LIB_ROOT") and os.environ.get("PDK_ROOT"):
        os.environ["PDK_LIB_ROOT"] = os.environ["PDK_ROOT"]

    tech = _expand_tree(yaml.safe_load((here / "tech" / args.tech / "tech.yaml").read_text()))
    src = Path(args.src_root).resolve() if args.src_root else here
    period = args.period or float(tech["limits"]["default_period_ns"])
    scl = args.scl or tech["scl"]
    variants = tech.get("scl_variants", {})
    if scl != tech["scl"] and scl not in variants:
        available = [tech["scl"]] + sorted(variants)
        raise SystemExit(
            f"tech {args.tech!r} does not define scl {scl!r}; "
            f"available: {available}"
        )
    variant = variants.get(scl, {})
    corners = variant.get("corners") or tech["corners"]

    if args.design in ("serdes_digital_top", "serdes_tx_digital",
                       "serdes_tx_stream_digital"):
        clock_port = "tx_clk"
    elif args.design == "serdes_rx_digital":
        clock_port = "rx_clk"
    else:
        clock_port = "clk"
    config = {
        "DESIGN_NAME": args.design,
        "VERILOG_FILES": [str(src / p) for p in DESIGNS[args.design]],
        "CLOCK_PORT": clock_port,
        "CLOCK_PERIOD": period,
        "PDK": tech["pdk"],
        "STD_CELL_LIBRARY": scl,
        "DEFAULT_CORNER": tech["default_corner"],
        "STA_CORNERS": list(corners.keys()),
        "LIB": {corner: [str(p) for p in paths] for corner, paths in corners.items()},
    }

    cts = tech.get("cts")
    if cts:
        # LibreLane/OpenROAD CTS: cap in pF, slew in ns.
        config["CTS_MAX_CAP"] = float(cts["max_cap_ff"]) / 1000.0
        config["CTS_MAX_SLEW"] = float(cts["max_slew_ns"])

    synth_params = []
    if args.capture_mode and args.design in ("serializer", "serdes_tx_digital", "serdes_digital_top"):
        synth_params.append("CAPTURE_MODE=1")
    if args.oversample and not args.cdr_bypass and args.design in ("serdes_digital_top", "serdes_rx_digital"):
        synth_params.append(f"OVERSAMPLE={args.oversample}")
    if args.cdr_bypass and args.design in ("serdes_digital_top", "serdes_rx_digital"):
        synth_params.append("CDR_BYPASS=1")
    if synth_params:
        config["SYNTH_PARAMETERS"] = synth_params

    if args.design == "serdes_digital_top":
        base_sdc = (here / "constraints" / "serdes_digital.sdc").read_text()
        marker = "set TX_PERIOD_NS 40.0"
        replacement = f"set TX_PERIOD_NS {period:.1f}"
    elif args.design == "serdes_tx_digital":
        base_sdc = (here / "constraints" / "block_tx.sdc").read_text()
        marker = "set CLOCK_PERIOD_NS 20.0"
        replacement = f"set CLOCK_PERIOD_NS {period:.1f}"
    elif args.design == "serdes_rx_digital":
        base_sdc = (here / "constraints" / "block_rx.sdc").read_text()
        marker = "set CLOCK_PERIOD_NS 5.0"
        replacement = f"set CLOCK_PERIOD_NS {period:.1f}"
    elif args.design == "serdes_tx_stream_digital":
        base_sdc = (here / "constraints" / "block_tx_stream.sdc").read_text()
        marker = "set CLOCK_PERIOD_NS 20.0"
        replacement = f"set CLOCK_PERIOD_NS {period:.1f}"
    elif args.design == "serializer":
        base_sdc = (here / "constraints" / "block_serializer.sdc").read_text()
        marker = "set CLOCK_PERIOD_NS 20.0"
        replacement = f"set CLOCK_PERIOD_NS {period:.1f}"
    else:
        base_sdc = (here / "constraints" / "block_deserializer.sdc").read_text()
        marker = "set CLOCK_PERIOD_NS 20.0"
        replacement = f"set CLOCK_PERIOD_NS {period:.1f}"
    if marker not in base_sdc:
        raise SystemExit(f"missing SDC period marker: {marker}")
    sdc_text = base_sdc.replace(marker, replacement)
    if args.design == "serdes_digital_top" and args.cdr_bypass:
        # In receiver-only mode the external rx_clk is the bit clock.
        sdc_text = sdc_text.replace("set OVERSAMPLE   4", "set OVERSAMPLE   1")
    elif args.design == "serdes_digital_top" and args.oversample:
        sdc_text = sdc_text.replace("set OVERSAMPLE   4", f"set OVERSAMPLE   {args.oversample}")
    sdc = Path(args.out).with_suffix(".sdc")
    sdc.write_text(sdc_text)
    config["PNR_SDC_FILE"] = str(sdc)
    config["SIGNOFF_SDC_FILE"] = str(sdc)

    Path(args.out).write_text(yaml.safe_dump(config, sort_keys=False))
    print(args.out)


if __name__ == "__main__":
    main()
