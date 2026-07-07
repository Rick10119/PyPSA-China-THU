#!/usr/bin/env python3
"""Recalculate solar-value results for thermal flexibility thresholds.

This is a post-processing sensitivity: every case reuses the same solved dispatch and
planning networks. Planning marginal prices remain the base price series; the
threshold-specific mapped-price CSV is used only as a zero-price mask.

Threshold 0.0 (``threshold_0/``) uses planning marginal prices directly (``--planning-marginal``),
matching the storage-x1 fill mode with no zero-price masking.

An additional ``threshold_0_sync/`` case uses minimum-output threshold 0.0 with only the
synchronous-generation floor zero mask (no low-output thresholding beyond sync floor).

For threshold > 0, mapped zero-price masks apply both the synchronous-generation floor
(10% local AC load) and ``daily_low_output_zero_threshold``. ``thermal_load_floor`` is
disabled so threshold cases differ only by the minimum-output parameter.
"""

from __future__ import annotations

import argparse
import copy
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORAGE_X1_CONFIG = (
    ROOT / "configs" / "storage_availability_sensitivity" / "config_storage_x1.yaml"
)
ZERO_SYNC_SLUG = "sync"


@dataclass(frozen=True)
class ThermalFlexCase:
    """One thermal-flexibility sensitivity case."""

    threshold: float
    slug: str = ""
    pure_lmp: bool = False

    @property
    def dir_name(self) -> str:
        base = f"threshold_{_tag(self.threshold)}"
        return f"{base}_{self.slug}" if self.slug else base

    @property
    def plot_label(self) -> str:
        if self.pure_lmp:
            return "min output = 0 (pure LMP)"
        if self.slug == ZERO_SYNC_SLUG:
            return "min output = 0 + sync floor"
        return f"min output = {self.threshold:g}"


def _tag(value: float) -> str:
    return f"{value:g}".replace("-", "m").replace(".", "p")


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def _default_config_path() -> Path:
    """Use the storage-x1 case as the default thermal-flexibility baseline."""
    return DEFAULT_STORAGE_X1_CONFIG if DEFAULT_STORAGE_X1_CONFIG.is_file() else ROOT / "config.yaml"


def _build_cases(thresholds: list[float], *, include_zero_sync_floor: bool) -> list[ThermalFlexCase]:
    cases: list[ThermalFlexCase] = []
    for threshold in thresholds:
        if float(threshold) == 0.0:
            cases.append(ThermalFlexCase(threshold=0.0, pure_lmp=True))
        else:
            cases.append(ThermalFlexCase(threshold=float(threshold)))
    if include_zero_sync_floor and any(float(t) == 0.0 for t in thresholds):
        cases.append(ThermalFlexCase(threshold=0.0, slug=ZERO_SYNC_SLUG))
    return cases


def _national_weighted_series(workbook: Path) -> pd.DataFrame:
    data = pd.read_excel(workbook, sheet_name="Sheet1", header=0)
    rows: list[dict[str, float | int]] = []
    for year, group in data.groupby("year", sort=True):
        weights = pd.to_numeric(group["solar_ele_GWh"], errors="coerce").fillna(0.0).clip(lower=0.0)
        values = pd.to_numeric(group["value_factor"], errors="coerce")
        valid = values.notna() & weights.notna()
        weights = weights.loc[valid]
        values = values.loc[valid]
        value_factor = (
            float(np.average(values, weights=weights)) if float(weights.sum()) > 0 else float(values.mean())
        )
        rows.append({"year": int(year), "value_factor": value_factor})
    return pd.DataFrame(rows)


def _plot_threshold_comparison(output_root: Path, cases: list[ThermalFlexCase]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9.2, 5.4))
    summary_frames: list[pd.DataFrame] = []
    for case in cases:
        workbook = output_root / case.dir_name / "solar_value_dataset.xlsx"
        if not workbook.is_file():
            continue
        series = _national_weighted_series(workbook)
        series.insert(0, "case", case.dir_name)
        series.insert(0, "slug", case.slug)
        series.insert(0, "pure_lmp", case.pure_lmp)
        series.insert(0, "threshold", float(case.threshold))
        summary_frames.append(series)
        ax.plot(
            series["year"],
            series["value_factor"],
            marker="o",
            linewidth=2.0,
            label=case.plot_label,
        )

    if not summary_frames:
        raise RuntimeError("No sensitivity workbooks were available for the comparison plot.")
    summary = pd.concat(summary_frames, ignore_index=True)
    summary.to_csv(output_root / "thermal_flexibility_value_factor_summary.csv", index=False)
    ax.set_xlabel("Year")
    ax.set_ylabel("Generation-weighted solar value factor")
    ax.set_title("Thermal flexibility sensitivity — national solar value factor")
    ax.grid(True, alpha=0.3)
    ax.legend(ncol=2, framealpha=0.92)
    fig.tight_layout()
    fig.savefig(output_root / "thermal_flexibility_value_factor_comparison.png", dpi=180)
    fig.savefig(output_root / "thermal_flexibility_value_factor_comparison.pdf")
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Export mapped prices and solar-value workbooks/figures for thermal thresholds."
    )
    ap.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Config to read solved networks from. Default: storage-x1 sensitivity config "
            "if it exists, otherwise config.yaml."
        ),
    )
    ap.add_argument("--threshold", type=float, action="append", dest="thresholds")
    ap.add_argument(
        "--template-workbook",
        type=Path,
        default=None,
        help="Workbook layout to copy for each case (default: current version solar_value_dataset.xlsx).",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Root output directory (default: <version_dir>/thermal_flexibility_sensitivity).",
    )
    ap.add_argument("--keep-price-plots", action="store_true", help="Also create exporter Shandong plots.")
    ap.add_argument(
        "--skip-zero-sync-floor",
        action="store_true",
        help="Do not run the extra threshold_0_sync case (0.0 minimum output + sync floor only).",
    )
    args = ap.parse_args()

    # Delay this import so ``--help`` also works in lightweight environments
    # where the full PyPSA runtime is not installed.
    from fill_solar_value_dataset_2025 import load_solar_value_fill_config

    config_path = (args.config or _default_config_path()).resolve()
    with config_path.open(encoding="utf-8") as f:
        raw_cfg = yaml.safe_load(f) or {}
    base = load_solar_value_fill_config(config_path)
    thresholds = args.thresholds or [0.4, 0.3, 0.2, 0.1, 0.0]
    if any(t < 0.0 or t > 1.0 for t in thresholds):
        ap.error("Every --threshold must be between 0 and 1.")
    cases = _build_cases(thresholds, include_zero_sync_floor=not args.skip_zero_sync_floor)

    template = (args.template_workbook or base.xlsx_path).resolve()
    if not template.is_file():
        raise FileNotFoundError(
            f"Template workbook not found: {template}. Pass --template-workbook explicitly."
        )
    output_root = (args.output_dir or base.version_dir / "thermal_flexibility_sensitivity").resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    exporter = ROOT / "scripts" / "export_reconstructed_prices.py"
    filler = ROOT / "scripts" / "fill_solar_value_dataset_2025.py"
    price_cfg = ((raw_cfg.get("dispatch_segmented_prices") or {}).get("price_export") or {})
    week_freq = str(price_cfg.get("week_freq", "W-SUN"))
    currency = str(price_cfg.get("currency", "CNY"))
    fx = str(float(price_cfg.get("fx_cny_per_eur", 7.8)))
    mapped_source = str(price_cfg.get("mapped_price_source", "reconstructed"))

    with tempfile.TemporaryDirectory(prefix="thermal-flex-config-", dir=config_path.parent) as tmp:
        tmp_dir = Path(tmp)
        for case in cases:
            case_dir = output_root / case.dir_name
            figure_dir = case_dir / "figures"
            figure_dir.mkdir(parents=True, exist_ok=True)
            config_tag = case.slug or _tag(case.threshold)

            if not case.pure_lmp:
                price_dir = case_dir / "mapped_prices"
                price_dir.mkdir(parents=True, exist_ok=True)

                case_cfg = copy.deepcopy(raw_cfg)
                pe = case_cfg.setdefault("dispatch_segmented_prices", {}).setdefault("price_export", {})
                pe["daily_low_output_zero_threshold"] = float(case.threshold)
                # Per-year values take precedence in the exporter, so clear them for a true uniform case.
                pe["daily_low_output_zero_threshold_by_year"] = {}
                # Keep sync-floor zero mask (default); disable mapped thermal_load_floor only.
                pe.setdefault("thermal_load_floor", {})["enabled"] = False
                case_config_path = tmp_dir / f"config_threshold_{config_tag}.yaml"
                with case_config_path.open("w", encoding="utf-8") as f:
                    yaml.safe_dump(case_cfg, f, allow_unicode=True, sort_keys=False)

                for year in base.target_years:
                    network = base.version_dir / "dispatch_segmented" / base.heating_demand / (
                        f"postnetwork-dispatch-seg-{base.scenario_stem}-{year}.nc"
                    )
                    planning = base.version_dir / "postnetworks" / base.heating_demand / (
                        f"postnetwork-{base.scenario_stem}-{year}.nc"
                    )
                    if not network.is_file():
                        print(f"Skip {year}: missing {network}")
                        continue
                    out_csv = price_dir / f"dispatch_segmented_prices-{base.scenario_stem}-{year}.csv"
                    cmd = [
                        sys.executable, str(exporter), "--network", str(network), "--out", str(out_csv),
                        "--config", str(case_config_path), "--week-freq", week_freq,
                        "--currency", currency, "--fx-cny-per-eur", fx,
                        "--mapped-price-source", mapped_source,
                    ]
                    if planning.is_file():
                        cmd += ["--baseline-network", str(planning), "--calibrate-max-with-baseline"]
                    if not args.keep_price_plots:
                        cmd.append("--skip-shandong-plot")
                    _run(cmd)
            else:
                print(
                    f"{case.dir_name}: using pure planning marginal prices "
                    "(same fill mode as storage-x1; skipping mapped-price export)"
                )

            workbook = case_dir / "solar_value_dataset.xlsx"
            workbook.write_bytes(template.read_bytes())
            fill_cmd = [
                sys.executable, str(filler), "--config", str(config_path),
                "--workbook", str(workbook), "--figure-dir", str(figure_dir),
            ]
            if case.pure_lmp:
                fill_cmd.append("--planning-marginal")
            else:
                fill_cmd += ["--allow-zero-price", "--mapped-price-dir", str(price_dir)]
            _run(fill_cmd)

    _plot_threshold_comparison(output_root, cases)
    print(f"All sensitivity cases written under: {output_root}")


if __name__ == "__main__":
    main()
