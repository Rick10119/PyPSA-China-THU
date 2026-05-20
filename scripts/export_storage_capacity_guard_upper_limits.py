#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

from storage_capacity_guard import _get_provincial_2025_baseline_mw, _resolve_repo_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export per-province new-build battery caps from storage_capacity_guard."
    )
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

    guard_cfg = config.get("storage_capacity_guard", {})
    if not bool(guard_cfg.get("enabled", False)):
        raise ValueError("storage_capacity_guard.enabled is false.")

    baseline = _get_provincial_2025_baseline_mw(guard_cfg)
    multiplier = float(guard_cfg.get("new_build_cap_multiplier", 1.0))
    max_hours = float(
        config.get("electricity", {}).get("max_hours", {}).get("battery", 6.0)
    )
    apply_start_year = int(guard_cfg.get("apply_start_year", 2030))
    apply_end_year = int(guard_cfg.get("apply_end_year", 2060))

    target_path = _resolve_repo_path(
        guard_cfg.get(
            "national_capacity_csv",
            "data/p_nom/national_battery_capacity_from_planning.csv",
        )
    )
    targets = pd.read_csv(target_path).set_index("year") if target_path.exists() else None

    rows = []
    for year in years:
        guard_active = apply_start_year <= year <= apply_end_year
        national_target = (
            float(targets.at[year, "national_battery_capacity_mw"])
            if targets is not None and year in targets.index
            else float("nan")
        )
        for province, baseline_mw in baseline.items():
            cap_power = float(baseline_mw) * multiplier if guard_active else 0.0
            rows.append(
                {
                    "year": year,
                    "province": province,
                    "baseline_2025_power_mw": float(baseline_mw),
                    "max_new_build_power_mw": cap_power,
                    "max_new_build_energy_mwh": cap_power * max_hours if guard_active else 0.0,
                    "guard_active": guard_active,
                    "new_build_cap_multiplier": multiplier,
                    "national_cumulative_target_mw": national_target,
                }
            )

    out = Path(args.output) if args.output else (
        repo_root / "data" / "p_nom" / "storage_capacity_guard_upper_limits.csv"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"Output: {out}")


if __name__ == "__main__":
    main()
