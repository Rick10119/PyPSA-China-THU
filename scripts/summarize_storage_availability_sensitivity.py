#!/usr/bin/env python3
"""Summarize solar-value outputs from storage availability sensitivity cases."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
METRIC_COLUMNS = [
    "solar_ele_GWh",
    "value_factor_numerator",
    "value_factor_denominator",
    "value_factor",
    "solar_penetration",
    "solar_curtailment_rate",
    "solar_capacity_factor",
]


def _version_dir(config: dict[str, Any], root: Path) -> Path:
    return root / str(config.get("results_dir") or "results") / f"version-{config['version']}"


def _load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _case_rows_from_manifest(path: Path) -> list[tuple[float, float, float, str, Path]]:
    manifest = pd.read_csv(path)
    rows = []
    for _, row in manifest.iterrows():
        battery_factor = float(row.get("battery_cost_factor", 1.0))
        thermal_threshold = float(row.get("thermal_flexibility_threshold", 0.4))
        fill_price_mode = str(row.get("fill_price_mode", "allow-zero-price"))
        rows.append(
            (
                float(row["multiplier"]),
                battery_factor,
                thermal_threshold,
                fill_price_mode,
                Path(row["config"]).resolve(),
            )
        )
    return rows


def _case_rows_from_configs(paths: list[Path]) -> list[tuple[float, float, float, str, Path]]:
    rows = []
    for path in paths:
        cfg = _load_config(path)
        multiplier = float(
            (cfg.get("storage_capacity_guard") or {}).get(
                "target_capacity_multiplier",
                (cfg.get("sensitivity") or {}).get("storage_availability_multiplier", 1.0),
            )
        )
        market_scenario = ((cfg.get("aluminum") or {}).get("current_scenario") or {}).get(
            "market_opportunity", "mid"
        )
        market_factors = (
            ((cfg.get("aluminum") or {}).get("scenario_dimensions") or {})
            .get("market_opportunity", {})
            .get(market_scenario, {})
        )
        battery_factor = float(
            (cfg.get("sensitivity") or {}).get(
                "battery_cost_factor", market_factors.get("battery_cost_factor", 1.0)
            )
        )
        sensitivity = cfg.get("sensitivity") or {}
        thermal_threshold = float(sensitivity.get("thermal_flexibility_threshold", 0.4))
        fill_price_mode = str(sensitivity.get("fill_price_mode", "allow-zero-price"))
        rows.append((multiplier, battery_factor, thermal_threshold, fill_price_mode, path.resolve()))
    return rows


def _weighted_average(values: pd.Series, weights: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce")
    weights = pd.to_numeric(weights, errors="coerce").fillna(0.0).clip(lower=0.0)
    valid = values.notna() & weights.notna()
    if not valid.any():
        return float("nan")
    if float(weights.loc[valid].sum()) > 0:
        return float(np.average(values.loc[valid], weights=weights.loc[valid]))
    return float(values.loc[valid].mean())


def _read_workbook(
    workbook: Path,
    multiplier: float,
    battery_factor: float,
    thermal_threshold: float,
    fill_price_mode: str,
    version: str,
) -> pd.DataFrame:
    data = pd.read_excel(workbook, sheet_name="Sheet1", header=0)
    zone_col = "load_zone" if "load_zone" in data.columns else "zone"
    required = {zone_col, "year", *METRIC_COLUMNS}
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"{workbook} is missing columns: {sorted(missing)}")
    data = data[[zone_col, "year", *METRIC_COLUMNS]].copy()
    data = data.rename(columns={zone_col: "load_zone"})
    data["year"] = pd.to_numeric(data["year"], errors="coerce")
    data = data.dropna(subset=["year"])
    data["year"] = data["year"].astype(int)
    data.insert(0, "version", version)
    data.insert(0, "fill_price_mode", str(fill_price_mode))
    data.insert(0, "thermal_flexibility_threshold", float(thermal_threshold))
    data.insert(0, "battery_cost_factor", float(battery_factor))
    data.insert(0, "storage_multiplier", float(multiplier))
    return data


def _national_summary(province_detail: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (
        multiplier,
        battery_factor,
        thermal_threshold,
        fill_price_mode,
        version,
        year,
    ), group in province_detail.groupby(
        [
            "storage_multiplier",
            "battery_cost_factor",
            "thermal_flexibility_threshold",
            "fill_price_mode",
            "version",
            "year",
        ],
        sort=True,
    ):
        weights = group["solar_ele_GWh"]
        row: dict[str, float | int | str] = {
            "storage_multiplier": float(multiplier),
            "battery_cost_factor": float(battery_factor),
            "thermal_flexibility_threshold": float(thermal_threshold),
            "fill_price_mode": str(fill_price_mode),
            "version": str(version),
            "year": int(year),
            "solar_ele_GWh": float(pd.to_numeric(weights, errors="coerce").fillna(0.0).sum()),
        }
        for col in METRIC_COLUMNS:
            if col == "solar_ele_GWh":
                continue
            row[col] = _weighted_average(group[col], weights)
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["storage_multiplier", "battery_cost_factor", "year"])


def _plot_value_factor(summary: pd.DataFrame, output_dir: Path) -> tuple[Path, Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9.2, 5.4))
    for (multiplier, battery_factor), group in summary.groupby(
        ["storage_multiplier", "battery_cost_factor"], sort=True
    ):
        group = group.sort_values("year")
        ax.plot(
            group["year"],
            group["value_factor"],
            marker="o",
            linewidth=2.0,
            label=f"storage {float(multiplier):g}x / battery {float(battery_factor):g}x",
        )
    ax.set_xlabel("Year")
    ax.set_ylabel("Generation-weighted solar value factor")
    ax.set_title("Storage availability sensitivity - national solar value factor")
    ax.grid(True, alpha=0.3)
    ax.legend(ncol=2, framealpha=0.92)
    fig.tight_layout()
    png = output_dir / "storage_availability_value_factor_comparison.png"
    pdf = output_dir / "storage_availability_value_factor_comparison.pdf"
    fig.savefig(png, dpi=180)
    fig.savefig(pdf)
    plt.close(fig)
    return png, pdf


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Read storage sensitivity solar_value_dataset.xlsx files and export summary tables."
    )
    ap.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "configs" / "storage_availability_sensitivity" / "storage_availability_cases.csv",
    )
    ap.add_argument("--config", type=Path, action="append", dest="configs")
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "results" / "storage_availability_sensitivity_summary",
    )
    ap.add_argument("--allow-missing", action="store_true")
    args = ap.parse_args()

    if args.configs:
        cases = _case_rows_from_configs([p.resolve() for p in args.configs])
    elif args.manifest.is_file():
        cases = _case_rows_from_manifest(args.manifest.resolve())
    else:
        raise FileNotFoundError(
            f"No manifest found at {args.manifest}. Pass --config for each case or generate cases first."
        )

    detail_frames = []
    missing = []
    for multiplier, battery_factor, thermal_threshold, fill_price_mode, config_path in cases:
        cfg = _load_config(config_path)
        version = str(cfg["version"])
        workbook = _version_dir(cfg, ROOT) / "solar_value_dataset.xlsx"
        if not workbook.is_file():
            missing.append(str(workbook))
            continue
        detail_frames.append(
            _read_workbook(
                workbook,
                multiplier,
                battery_factor,
                thermal_threshold,
                fill_price_mode,
                version,
            )
        )

    if missing and not args.allow_missing:
        raise FileNotFoundError("Missing workbook(s):\n" + "\n".join(missing))
    if not detail_frames:
        if args.allow_missing:
            print("No sensitivity workbooks were available to summarize yet.")
            for item in missing:
                print(f"  missing: {item}")
            return
        raise RuntimeError("No sensitivity workbooks were available to summarize.")

    detail = pd.concat(detail_frames, ignore_index=True)
    summary = _national_summary(detail)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    detail_csv = args.output_dir / "storage_availability_province_detail.csv"
    summary_csv = args.output_dir / "storage_availability_national_summary.csv"
    xlsx = args.output_dir / "storage_availability_sensitivity_summary.xlsx"
    detail.to_csv(detail_csv, index=False)
    summary.to_csv(summary_csv, index=False)
    with pd.ExcelWriter(xlsx) as writer:
        summary.to_excel(writer, sheet_name="national_summary", index=False)
        detail.to_excel(writer, sheet_name="province_detail", index=False)
    png, pdf = _plot_value_factor(summary, args.output_dir)

    print(f"National summary: {summary_csv}")
    print(f"Province detail: {detail_csv}")
    print(f"Workbook: {xlsx}")
    print(f"Value-factor figure: {png}")
    print(f"Value-factor figure PDF: {pdf}")
    if missing:
        print("Skipped missing workbook(s):")
        for item in missing:
            print(f"  {item}")


if __name__ == "__main__":
    main()
