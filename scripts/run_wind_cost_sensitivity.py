#!/usr/bin/env python3
"""Run wind-cost sensitivity cases and compare solar value factors.

By default, regenerates each case config as a full copy of ``--config``
(``config.yaml``), then applies only wind capital-cost / wind-guard overrides.
Solar-value filling matches the storage-x1 / thermal-flexibility 40% baseline
(``--allow-zero-price`` with ``daily_low_output_zero_threshold = 0.4``).
Solar value-factor comparison still defaults to the storage-x1 baseline workbook.
"""

from __future__ import annotations

import argparse
import copy
import csv
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "configs" / "wind_cost_sensitivity" / "wind_cost_sensitivity_cases.csv"
DEFAULT_CONFIG_DIR = ROOT / "configs" / "wind_cost_sensitivity"
DEFAULT_SOURCE_CONFIG = ROOT / "config.yaml"
DEFAULT_COMPARISON_CONFIG = ROOT / "configs" / "storage_availability_sensitivity" / "config_storage_x1.yaml"
DEFAULT_FILL_PRICE_MODE = "allow-zero-price"
DEFAULT_THERMAL_FLEXIBILITY_THRESHOLD = 0.4
DEFAULT_CASES = (
    # case, wind_cost_factor, wind_target_upper_multiplier, notes
    ("wind_cheap_x0p8", 0.8, 1.5, "Wind capital cost 0.8x; wind guard upper bound 1.5x target"),
    ("wind_cheap_x0p6", 0.6, 2.0, "Wind capital cost 0.6x; wind guard upper bound 2.0x target"),
    ("wind_cheap_x0p4", 0.4, 2.5, "Wind capital cost 0.4x; wind guard upper bound 2.5x target"),
)
FILL_PRICE_MODE_ARGS = {
    "planning-marginal": "--planning-marginal",
    "mapped-csv": "--mapped-csv",
    "allow-zero-price": "--allow-zero-price",
}


def _load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _tag(value: float) -> str:
    return f"{value:g}".replace("-", "m").replace(".", "p")


def _version_dir(cfg: dict) -> Path:
    return ROOT / str(cfg.get("results_dir") or "results") / f"version-{cfg['version']}"


def _scenario_stem(cfg: dict) -> str:
    scen = cfg.get("scenario") or {}

    def first(value, default):
        if isinstance(value, list):
            return str(value[0]) if value else default
        return str(value) if value is not None else default

    return f"{first(scen.get('opts'), 'll')}-{scen.get('topology', 'current+FCG')}-{first(scen.get('pathway'), 'linear2050')}"


def _heating(cfg: dict) -> str:
    heating = (cfg.get("scenario") or {}).get("heating_demand", "positive")
    if isinstance(heating, list):
        return str(heating[0]) if heating else "positive"
    return str(heating)


def _target_years(cfg: dict) -> list[int]:
    years = (cfg.get("scenario") or {}).get("planning_horizons")
    return [int(y) for y in years] if years else list(range(2025, 2065, 5))


def _run(cmd: list[str], *, cwd: Path = ROOT) -> None:
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def _ensure_template_workbook(version_dir: Path, template: Path) -> Path:
    workbook = version_dir / "solar_value_dataset.xlsx"
    version_dir.mkdir(parents=True, exist_ok=True)
    if not workbook.exists():
        shutil.copy2(template, workbook)
    return workbook


def _read_weighted_value_factor(workbook: Path) -> pd.DataFrame:
    data = pd.read_excel(workbook, sheet_name="Sheet1")
    rows = []
    for year, group in data.groupby("year", sort=True):
        weights = pd.to_numeric(group["solar_ele_GWh"], errors="coerce").fillna(0.0).clip(lower=0.0)
        values = pd.to_numeric(group["value_factor"], errors="coerce")
        valid = values.notna() & weights.notna()
        if not valid.any():
            value = float("nan")
        elif float(weights.loc[valid].sum()) > 0:
            value = float(np.average(values.loc[valid], weights=weights.loc[valid]))
        else:
            value = float(values.loc[valid].mean())
        rows.append({"year": int(year), "value_factor": value, "solar_ele_GWh": float(weights.sum())})
    return pd.DataFrame(rows)


def _plot_solar_comparison(summary: pd.DataFrame, output_png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.5), sharex=True)
    for case, group in summary.groupby("case", sort=False):
        group = group.sort_values("year")
        axes[0].plot(group["year"], group["value_factor"], marker="o", label=case)
        if not str(case).startswith("baseline_"):
            axes[1].plot(group["year"], group["delta_vs_baseline"], marker="o", label=case)
    axes[0].set_ylabel("Solar value factor")
    axes[1].set_ylabel("Delta vs baseline")
    axes[1].axhline(0.0, color="black", linewidth=1.0, linestyle="--")
    for ax in axes:
        ax.set_xlabel("Year")
        ax.grid(True, alpha=0.3)
    axes[0].legend(framealpha=0.9)
    axes[1].legend(framealpha=0.9)
    fig.tight_layout()
    fig.savefig(output_png, dpi=180)
    plt.close(fig)


def _case_specs_from_manifest(manifest: Path) -> list[tuple[str, float, float, str]]:
    if not manifest.is_file():
        return list(DEFAULT_CASES)
    rows = pd.read_csv(manifest).to_dict("records")
    specs = []
    for row in rows:
        specs.append(
            (
                str(row["case"]),
                float(row["wind_cost_factor"]),
                float(row["wind_target_upper_multiplier"]),
                str(row.get("notes") or ""),
            )
        )
    return specs or list(DEFAULT_CASES)


def _apply_wind_case(
    base_cfg: dict[str, Any],
    *,
    case: str,
    wind_cost_factor: float,
    wind_target_upper_multiplier: float,
    version_prefix: str,
    fill_price_mode: str,
    thermal_flexibility_threshold: float,
) -> dict[str, Any]:
    case_cfg = copy.deepcopy(base_cfg)
    tag = case.removeprefix("wind_cheap_") if case.startswith("wind_cheap_") else f"x{_tag(wind_cost_factor)}"
    case_cfg["version"] = f"{version_prefix}-{tag}"

    case_cfg.setdefault("wind_capacity_guard", {})["target_upper_multiplier"] = float(
        wind_target_upper_multiplier
    )

    market_mid = (
        case_cfg.setdefault("aluminum", {})
        .setdefault("scenario_dimensions", {})
        .setdefault("market_opportunity", {})
        .setdefault("mid", {})
    )
    # Keep solar at core; only wind capital cost is scaled.
    market_mid["solar_cost_factor"] = 1.0
    market_mid["wind_cost_factor"] = float(wind_cost_factor)

    # Match storage-x1 / thermal-flexibility baseline: 40% daily low-output zero mask.
    price_export = (
        case_cfg.setdefault("dispatch_segmented_prices", {})
        .setdefault("price_export", {})
    )
    price_export["daily_low_output_zero_threshold"] = float(thermal_flexibility_threshold)

    sensitivity = case_cfg.setdefault("sensitivity", {})
    sensitivity["wind_cost_factor"] = float(wind_cost_factor)
    sensitivity["wind_target_upper_multiplier"] = float(wind_target_upper_multiplier)
    sensitivity["thermal_flexibility_baseline"] = f"threshold_{_tag(thermal_flexibility_threshold)}"
    sensitivity["thermal_flexibility_threshold"] = float(thermal_flexibility_threshold)
    sensitivity["fill_price_mode"] = str(fill_price_mode)
    return case_cfg


def _update_case_configs(
    *,
    baseline_cfg: dict[str, Any],
    config_dir: Path,
    manifest: Path,
    version_prefix: str,
    case_specs: list[tuple[str, float, float, str]],
    fill_price_mode: str,
    thermal_flexibility_threshold: float,
) -> list[dict]:
    config_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    print("Updating wind-cost sensitivity configs from source config:", flush=True)
    for case, wind_cost_factor, wind_upper, notes in case_specs:
        case_cfg = _apply_wind_case(
            baseline_cfg,
            case=case,
            wind_cost_factor=wind_cost_factor,
            wind_target_upper_multiplier=wind_upper,
            version_prefix=version_prefix,
            fill_price_mode=fill_price_mode,
            thermal_flexibility_threshold=thermal_flexibility_threshold,
        )
        cfg_path = config_dir / f"config_{case}.yaml"
        with cfg_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(case_cfg, f, allow_unicode=True, sort_keys=False)
        print(
            f"  {case}: wind_cost={wind_cost_factor:g}x, guard_upper={wind_upper:g}x -> "
            f"{cfg_path} | version-{case_cfg['version']}",
            flush=True,
        )
        rows.append(
            {
                "case": case,
                "config": str(cfg_path.relative_to(ROOT)),
                "wind_cost_factor": float(wind_cost_factor),
                "wind_target_upper_multiplier": float(wind_upper),
                "notes": notes,
            }
        )

    with manifest.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "case",
                "config",
                "wind_cost_factor",
                "wind_target_upper_multiplier",
                "notes",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {manifest}", flush=True)
    return rows


def _case_rows(manifest_rows: list[dict]) -> list[dict]:
    rows = []
    for row in manifest_rows:
        item = dict(row)
        item["config"] = (ROOT / str(row["config"])).resolve()
        item["wind_cost_factor"] = float(row["wind_cost_factor"])
        item["wind_target_upper_multiplier"] = float(row["wind_target_upper_multiplier"])
        rows.append(item)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_SOURCE_CONFIG,
        help="Source config to deepcopy into each wind-cheap case (default: config.yaml).",
    )
    ap.add_argument(
        "--baseline-config",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,  # backward-compatible alias for --config
    )
    ap.add_argument(
        "--comparison-config",
        type=Path,
        default=DEFAULT_COMPARISON_CONFIG if DEFAULT_COMPARISON_CONFIG.is_file() else DEFAULT_SOURCE_CONFIG,
        help=(
            "Config whose solar_value_dataset.xlsx is used as the comparison baseline "
            "(default: storage-x1 if present, else config.yaml)."
        ),
    )
    ap.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    ap.add_argument(
        "--version-prefix",
        default=None,
        help=(
            "Prefix for case versions. Default: '<source-version>-wind-cheap', "
            "e.g. config.yaml version 0708.1H.1 -> version-0708.1H.1-wind-cheap-x0p8."
        ),
    )
    ap.add_argument(
        "--skip-config-update",
        action="store_true",
        help="Do not regenerate case configs from --config; use existing files.",
    )
    ap.add_argument(
        "--configs-only",
        action="store_true",
        help="Only regenerate case configs/manifest from --config, then exit.",
    )
    ap.add_argument(
        "--fill-price-mode",
        choices=sorted(FILL_PRICE_MODE_ARGS),
        default=DEFAULT_FILL_PRICE_MODE,
        help=(
            "Price source used when filling solar_value_dataset.xlsx after each wind-cost run. "
            "Default matches the storage-x1 / thermal-flexibility 40% baseline "
            "(planning LMP with mapped zero-price hours forced to zero)."
        ),
    )
    ap.add_argument(
        "--thermal-flexibility-threshold",
        type=float,
        default=DEFAULT_THERMAL_FLEXIBILITY_THRESHOLD,
        help=(
            "daily_low_output_zero_threshold written into each case config and used by "
            "dispatch_segmented price export (default: 0.4)."
        ),
    )
    ap.add_argument("--template-workbook", type=Path, default=None)
    ap.add_argument("--output-dir", type=Path, default=ROOT / "results" / "wind_cost_sensitivity_summary")
    ap.add_argument("--cores", type=int, default=32)
    ap.add_argument("--snakemake", default="snakemake")
    ap.add_argument("--skip-snakemake", action="store_true")
    ap.add_argument("--skip-fill", action="store_true")
    ap.add_argument("--skip-plots-in-fill", action="store_true", default=True)
    ap.add_argument("--snakemake-extra-args", nargs=argparse.REMAINDER, default=[])
    args = ap.parse_args()

    if args.configs_only and args.skip_config_update:
        ap.error("--configs-only cannot be combined with --skip-config-update")
    if not 0.0 <= float(args.thermal_flexibility_threshold) <= 1.0:
        ap.error("--thermal-flexibility-threshold must be between 0 and 1")

    source_path = (args.baseline_config or args.config).resolve()
    source_cfg = _load_config(source_path)
    if "version" not in source_cfg:
        raise KeyError(f"Source config must define 'version': {source_path}")

    case_specs = _case_specs_from_manifest(args.manifest.resolve())
    if args.skip_config_update:
        if not args.manifest.is_file():
            raise FileNotFoundError(
                f"--skip-config-update requires an existing manifest: {args.manifest}"
            )
        manifest_rows = pd.read_csv(args.manifest.resolve()).to_dict("records")
        print(f"Using existing configs from {args.manifest} (config update skipped).", flush=True)
    else:
        print(f"Source config for case regeneration: {source_path}", flush=True)
        version_prefix = args.version_prefix or f"{source_cfg['version']}-wind-cheap"
        manifest_rows = _update_case_configs(
            baseline_cfg=source_cfg,
            config_dir=args.config_dir.resolve(),
            manifest=args.manifest.resolve(),
            version_prefix=version_prefix,
            case_specs=case_specs,
            fill_price_mode=str(args.fill_price_mode),
            thermal_flexibility_threshold=float(args.thermal_flexibility_threshold),
        )

    if args.configs_only:
        return

    comparison_path = args.comparison_config.resolve()
    comparison_cfg = _load_config(comparison_path)
    comparison_version_dir = _version_dir(comparison_cfg)
    baseline_workbook = (
        args.template_workbook.resolve()
        if args.template_workbook is not None
        else comparison_version_dir / "solar_value_dataset.xlsx"
    )
    if not baseline_workbook.is_file():
        raise FileNotFoundError(f"Baseline/template workbook not found: {baseline_workbook}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    cases = _case_rows(manifest_rows)

    capacity_frames = []
    solar_frames = []
    baseline_series = _read_weighted_value_factor(baseline_workbook)
    baseline_series.insert(0, "wind_target_upper_multiplier", np.nan)
    baseline_series.insert(0, "wind_cost_factor", np.nan)
    baseline_case = (
        "baseline_storage_x1"
        if "storage-x1" in str(comparison_cfg.get("version", ""))
        else f"baseline_{comparison_cfg.get('version', 'config')}"
    )
    baseline_series.insert(0, "case", baseline_case)
    solar_frames.append(baseline_series)

    for row in cases:
        case = str(row["case"])
        cfg_path = Path(row["config"])
        cfg = _load_config(cfg_path)
        version_dir = _version_dir(cfg)
        print(f"\n=== {case}: {cfg_path} ===", flush=True)

        if not args.skip_snakemake:
            cmd = [
                args.snakemake,
                "--configfile",
                str(cfg_path),
                "--cores",
                str(args.cores),
                *args.snakemake_extra_args,
            ]
            _run(cmd)

        workbook = _ensure_template_workbook(version_dir, baseline_workbook)
        if not args.skip_fill:
            fill_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "fill_solar_value_dataset_2025.py"),
                "--config",
                str(cfg_path),
                "--workbook",
                str(workbook),
                FILL_PRICE_MODE_ARGS[str(args.fill_price_mode)],
            ]
            if args.skip_plots_in_fill:
                fill_cmd.append("--skip-plot")
            _run(fill_cmd)

        capacity_csv = args.output_dir / f"wind_solar_cost_capacity_{case}.csv"
        capacity_png = args.output_dir / f"wind_solar_cost_capacity_{case}.png"
        diag_cmd = [
            sys.executable,
            str(ROOT / "scripts" / "summarize_wind_solar_cost_capacity.py"),
            "--config",
            str(cfg_path),
            "--output-csv",
            str(capacity_csv),
            "--output-png",
            str(capacity_png),
        ]
        _run(diag_cmd)
        cap = pd.read_csv(capacity_csv)
        cap.insert(0, "case", case)
        cap.insert(1, "wind_cost_factor", float(row["wind_cost_factor"]))
        cap.insert(2, "wind_target_upper_multiplier", float(row["wind_target_upper_multiplier"]))
        capacity_frames.append(cap)

        series = _read_weighted_value_factor(workbook)
        series.insert(0, "wind_target_upper_multiplier", float(row["wind_target_upper_multiplier"]))
        series.insert(0, "wind_cost_factor", float(row["wind_cost_factor"]))
        series.insert(0, "case", case)
        solar_frames.append(series)

    solar = pd.concat(solar_frames, ignore_index=True)
    baseline_by_year = baseline_series.set_index("year")["value_factor"]
    solar["baseline_value_factor"] = solar["year"].map(baseline_by_year)
    solar["delta_vs_baseline"] = solar["value_factor"] - solar["baseline_value_factor"]
    solar["pct_delta_vs_baseline"] = solar["delta_vs_baseline"] / solar["baseline_value_factor"]
    solar_csv = args.output_dir / "wind_cost_solar_value_factor_comparison.csv"
    solar.to_csv(solar_csv, index=False)
    _plot_solar_comparison(solar, args.output_dir / "wind_cost_solar_value_factor_comparison.png")
    print(f"Wrote {solar_csv}")
    print(f"Wrote {args.output_dir / 'wind_cost_solar_value_factor_comparison.png'}")

    if capacity_frames:
        capacity = pd.concat(capacity_frames, ignore_index=True)
        capacity_csv = args.output_dir / "wind_cost_capacity_comparison.csv"
        capacity.to_csv(capacity_csv, index=False)
        print(f"Wrote {capacity_csv}")

    xlsx = args.output_dir / "wind_cost_sensitivity_summary.xlsx"
    with pd.ExcelWriter(xlsx) as writer:
        solar.to_excel(writer, sheet_name="solar_value_factor", index=False)
        if capacity_frames:
            capacity.to_excel(writer, sheet_name="wind_solar_capacity_cost", index=False)
    print(f"Wrote {xlsx}")


if __name__ == "__main__":
    main()
