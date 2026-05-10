#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

from solar_capacity_guard import _get_historical_solar_shares, _get_historical_2025_baseline_mw


def main() -> None:
    parser = argparse.ArgumentParser(description="Export solar guard upper limits to CSV.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    repo_root = config_path.parent
    config = yaml.safe_load(config_path.read_text()) or {}

    scenario = config.get("scenario", {})
    years = [int(y) for y in scenario.get("planning_horizons", [])]
    if not years:
        raise ValueError("No planning_horizons in config.")

    guard_cfg = config.get("solar_capacity_guard", {})
    if not bool(guard_cfg.get("enabled", False)):
        raise ValueError("solar_capacity_guard.enabled is false.")

    tol = float(guard_cfg.get("tolerance", 0.2))
    allow_underbuild_only = bool(guard_cfg.get("allow_underbuild_only", True))
    lower_mult = 0.0 if allow_underbuild_only else max(0.0, 1.0 - tol)
    upper_mult = 1.0 if allow_underbuild_only else 1.0 + tol

    # national targets
    target_csv = guard_cfg.get("national_capacity_csv", "data/p_nom/national_solar_capacity_from_planning_nc.csv")
    target_path = Path(target_csv)
    if not target_path.is_absolute():
        target_path = repo_root / target_path
    targets = pd.read_csv(target_path).set_index("year")

    # provincial allocation shares are always historical in the new rule
    shares = _get_historical_solar_shares(config, guard_cfg)

    results_dir = str(config.get("results_dir", "results/")).strip()
    version = str(config.get("version", "")).strip()
    apply_start_year = int(guard_cfg.get("apply_start_year", 2025))
    apply_end_year = int(guard_cfg.get("apply_end_year", 2060))
    use_historical_2025_baseline = bool(guard_cfg.get("use_historical_2025_baseline", True))
    rows = []
    for year in years:
        if year == 2025 and use_historical_2025_baseline:
            national_target = _get_historical_2025_baseline_mw(config, guard_cfg)
            target_source = "historical_cumulative_2025"
        else:
            national_target = float(targets.at[year, "national_solar_capacity_mw"]) if year in targets.index else float("nan")
            target_source = "national_capacity_csv" if year in targets.index else "missing_target"
        guard_active = (apply_start_year <= year <= apply_end_year)

        for bus, share in shares.items():
            min_total = national_target * float(share) * lower_mult if pd.notna(national_target) else float("nan")
            max_total = national_target * float(share) * upper_mult if pd.notna(national_target) else float("nan")

            rows.append(
                {
                    "year": year,
                    "province": bus,
                    "share_historical": float(share),
                    "national_target_mw": national_target,
                    "target_source": target_source,
                    "guard_active": guard_active,
                    "allocated_min_total_mw": min_total,
                    "allocated_max_total_mw": max_total,
                    "allow_underbuild_only": allow_underbuild_only,
                }
            )

    out = Path(args.output) if args.output else (
        repo_root / "data" / "p_nom" / "solar_capacity_guard_upper_limits.csv"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"Output: {out}")


if __name__ == "__main__":
    main()
