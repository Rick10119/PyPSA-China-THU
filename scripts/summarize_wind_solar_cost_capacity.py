#!/usr/bin/env python3
"""Summarize wind/solar model costs and capacity-guard utilization."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import xarray as xr
import yaml


ROOT = Path(__file__).resolve().parents[1]


def _read_generators(network: Path) -> pd.DataFrame:
    ds = xr.open_dataset(network)
    idx = pd.Index(ds["generators_i"].values.astype(str), name="generator")
    return pd.DataFrame(
        {
            "carrier": ds["generators_carrier"].values.astype(str),
            "p_nom_opt": ds["generators_p_nom_opt"].values.astype(float),
            "capital_cost": ds["generators_capital_cost"].values.astype(float),
        },
        index=idx,
    )


def _network_path(version_dir: Path, heating: str, stem: str, year: int) -> Path:
    return version_dir / "postnetworks" / heating / f"postnetwork-{stem}-{year}.nc"


def _targets(years: list[int], solar_csv: Path, wind_csv: Path, upper_multiplier: float) -> pd.DataFrame:
    solar = pd.read_csv(solar_csv).set_index("year")
    wind = pd.read_csv(wind_csv).set_index("year")
    rows = []
    for year in years:
        rows.append(
            {
                "year": year,
                "carrier": "solar",
                "target_mw": float(solar.at[year, "national_solar_capacity_mw"]),
            }
        )
        rows.append(
            {
                "year": year,
                "carrier": "onwind",
                "target_mw": float(wind.at[year, "national_onwind_capacity_mw"]),
            }
        )
        rows.append(
            {
                "year": year,
                "carrier": "offwind",
                "target_mw": float(wind.at[year, "national_offwind_capacity_mw"]),
            }
        )
    out = pd.DataFrame(rows)
    out["guard_upper_mw"] = out["target_mw"] * float(upper_multiplier)
    return out


def _summarize(args: argparse.Namespace) -> pd.DataFrame:
    years = [int(y) for y in args.years.split(",") if str(y).strip()]
    target = _targets(
        years,
        args.solar_target_csv.resolve(),
        args.wind_target_csv.resolve(),
        float(args.guard_upper_multiplier),
    )
    rows = []
    for year in years:
        network = _network_path(args.version_dir.resolve(), args.heating, args.scenario_stem, year)
        if not network.is_file():
            raise FileNotFoundError(network)
        gens = _read_generators(network)
        for carrier in ["solar", "onwind", "offwind"]:
            subset = gens[gens["carrier"].eq(carrier)]
            built = float(subset["p_nom_opt"].sum())
            annual_cost = (
                float((subset["capital_cost"] * subset["p_nom_opt"]).sum() / built)
                if built > 0.0
                else float("nan")
            )
            rows.append(
                {
                    "year": year,
                    "carrier": carrier,
                    "built_mw": built,
                    "annual_capital_cost_eur_per_mw_yr": annual_cost,
                }
            )
    out = pd.DataFrame(rows).merge(target, on=["year", "carrier"], how="left")
    out["built_vs_target"] = out["built_mw"] / out["target_mw"]
    out["built_vs_guard_upper"] = out["built_mw"] / out["guard_upper_mw"]
    return out


def _plot(summary: pd.DataFrame, output_png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    carriers = ["solar", "onwind", "offwind"]
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.4), sharex=True)
    for carrier in carriers:
        g = summary[summary["carrier"].eq(carrier)].sort_values("year")
        axes[0].plot(g["year"], g["annual_capital_cost_eur_per_mw_yr"], marker="o", label=carrier)
        axes[1].plot(g["year"], g["built_vs_guard_upper"], marker="o", label=carrier)
    axes[0].set_ylabel("Annual capital cost [EUR/MW/yr]")
    axes[1].set_ylabel("Built capacity / guard upper bound")
    for ax in axes:
        ax.set_xlabel("Year")
        ax.grid(True, alpha=0.3)
    axes[1].axhline(1.0, color="black", linewidth=1.0, linestyle="--")
    axes[0].legend(framealpha=0.9)
    fig.tight_layout()
    fig.savefig(output_png, dpi=180)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=None, help="Optional config file to infer version and guard settings.")
    ap.add_argument("--version-dir", type=Path, default=None)
    ap.add_argument("--scenario-stem", default="ll-current+FCG-linear2050")
    ap.add_argument("--heating", default="positive")
    ap.add_argument("--years", default="2025,2030,2035,2040,2045,2050,2055,2060")
    ap.add_argument(
        "--solar-target-csv",
        type=Path,
        default=ROOT / "data" / "p_nom" / "national_solar_capacity_from_external_targets.csv",
    )
    ap.add_argument(
        "--wind-target-csv",
        type=Path,
        default=ROOT / "data" / "p_nom" / "national_wind_capacity_from_planning.csv",
    )
    ap.add_argument("--guard-upper-multiplier", type=float, default=1.3)
    ap.add_argument("--output-csv", type=Path, required=True)
    ap.add_argument("--output-png", type=Path, default=None)
    args = ap.parse_args()

    if args.config is not None:
        with args.config.open(encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        if args.version_dir is None:
            results_dir = Path(str(cfg.get("results_dir") or "results"))
            args.version_dir = (ROOT / results_dir / f"version-{cfg['version']}").resolve()
        guard_cfg = cfg.get("wind_capacity_guard") or {}
        args.guard_upper_multiplier = float(
            guard_cfg.get("target_upper_multiplier", args.guard_upper_multiplier)
        )
        args.wind_target_csv = (ROOT / str(guard_cfg.get("national_capacity_csv", args.wind_target_csv))).resolve()
        solar_guard_cfg = cfg.get("solar_capacity_guard") or {}
        args.solar_target_csv = (
            ROOT / str(solar_guard_cfg.get("national_capacity_csv", args.solar_target_csv))
        ).resolve()
    if args.version_dir is None:
        ap.error("--version-dir is required unless --config is provided")

    summary = _summarize(args)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(args.output_csv, index=False)
    print(f"Wrote {args.output_csv}")
    if args.output_png is not None:
        args.output_png.parent.mkdir(parents=True, exist_ok=True)
        _plot(summary, args.output_png)
        print(f"Wrote {args.output_png}")


if __name__ == "__main__":
    main()
