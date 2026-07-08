#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import yaml

from storage_capacity_guard import (
    _get_provincial_2025_baseline_mw,
    _resolve_repo_path,
    _target_capacity_multiplier,
    _target_multipliers,
    _yearly_min_capacity_mw,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export national new-build battery caps from storage_capacity_guard."
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

    fixed_power = float(_get_provincial_2025_baseline_mw(guard_cfg).sum())
    lower_mult, upper_mult = _target_multipliers(guard_cfg)
    capacity_multiplier = _target_capacity_multiplier(guard_cfg)
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
        national_target_raw = (
            float(targets.at[year, "national_battery_capacity_mw"])
            if targets is not None and year in targets.index
            else float("nan")
        )
        national_target = national_target_raw * capacity_multiplier if pd.notna(national_target_raw) else float("nan")
        target_power = national_target if guard_active and pd.notna(national_target) else 0.0
        min_cumulative_power = (
            max(target_power * lower_mult, _yearly_min_capacity_mw(guard_cfg, year))
            if guard_active and pd.notna(target_power)
            else 0.0
        )
        max_cumulative_power = target_power * upper_mult if guard_active and pd.notna(target_power) else 0.0
        if min_cumulative_power > max_cumulative_power:
            min_cumulative_power = max_cumulative_power
        fixed_power_applied = fixed_power if guard_active else 0.0
        min_power = max(min_cumulative_power - fixed_power_applied, 0.0) if guard_active else 0.0
        max_power = max(max_cumulative_power - fixed_power_applied, 0.0) if guard_active else 0.0
        rows.append(
            {
                "year": year,
                "region": "National",
                "target_total_power_mw": target_power,
                "min_cumulative_power_mw": min_cumulative_power,
                "max_cumulative_power_mw": max_cumulative_power,
                "fixed_power_subtracted_mw": fixed_power_applied,
                "min_extendable_power_mw": min_power,
                "max_extendable_power_mw": max_power,
                "min_extendable_energy_mwh": min_power * max_hours if guard_active else 0.0,
                "max_extendable_energy_mwh": max_power * max_hours if guard_active else 0.0,
                "guard_active": guard_active,
                "lower_multiplier": lower_mult,
                "upper_multiplier": upper_mult,
                "target_capacity_multiplier": capacity_multiplier,
                "national_cumulative_target_mw": national_target,
                "national_cumulative_target_mw_unscaled": national_target_raw,
                "allocation": "national_only",
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
