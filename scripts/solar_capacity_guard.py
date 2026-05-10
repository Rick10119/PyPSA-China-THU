import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _get_historical_2025_baseline_mw(config, guard_cfg: dict) -> float:
    """
    Compute national 2025 baseline from historical installed-capacity table.

    Default behavior:
    - read `data/existing_infrastructure/solar capacity.csv`
    - sum columns 2010/2015/2020/2025 across provinces
    """
    hist_path = Path(
        str(guard_cfg.get("historical_capacity_csv", "data/existing_infrastructure/solar capacity.csv"))
    )
    if not hist_path.is_absolute():
        hist_path = Path(__file__).resolve().parents[1] / hist_path
    if not hist_path.exists():
        raise FileNotFoundError(f"Historical solar capacity file not found: {hist_path}")

    year_cols = guard_cfg.get("historical_year_columns", ["2010", "2015", "2020", "2025"])
    if not isinstance(year_cols, list) or not year_cols:
        raise ValueError("solar_capacity_guard.historical_year_columns must be a non-empty list.")

    df = pd.read_csv(hist_path)
    missing = [c for c in year_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing historical year columns {missing} in {hist_path}")

    baseline_mw = float(df[year_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).sum().sum())
    return baseline_mw


def _get_historical_solar_shares(config, guard_cfg: dict) -> pd.Series:
    """
    Get provincial share vector from historical installed capacity table.
    Shares are based on sum(historical_year_columns) by province.
    """
    hist_path = Path(
        str(guard_cfg.get("historical_capacity_csv", "data/existing_infrastructure/solar capacity.csv"))
    )
    if not hist_path.is_absolute():
        hist_path = Path(__file__).resolve().parents[1] / hist_path
    if not hist_path.exists():
        raise FileNotFoundError(f"Historical solar capacity file not found: {hist_path}")

    year_cols = guard_cfg.get("historical_year_columns", ["2010", "2015", "2020", "2025"])
    if "Region" not in pd.read_csv(hist_path, nrows=1).columns:
        raise ValueError(f"'Region' column missing in {hist_path}")

    df = pd.read_csv(hist_path)
    missing = [c for c in year_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing historical year columns {missing} in {hist_path}")

    df = df.copy()
    df[year_cols] = df[year_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    by_region = df.groupby("Region")[year_cols].sum().sum(axis=1)
    total = float(by_region.sum())
    if total <= 0:
        raise ValueError(f"Historical total solar capacity is non-positive in {hist_path}")
    shares = by_region / total
    return shares


def apply_solar_capacity_guard(n, config, scenario_context: dict | None = None):
    """
    Apply provincial min/max solar capacity bounds from national targets.

    - Reads national targets from data/p_nom/national_solar_capacity_from_planning_nc.csv
    - Allocates by reference-year (default 2025) provincial shares
    - Applies +/- tolerance (default 20%)
    - If derived provincial min exceeds potential, drop target min and keep only potential upper bound
    """
    guard_cfg = config.get("solar_capacity_guard", {})
    if not bool(guard_cfg.get("enabled", False)):
        return

    planning_year = int(pd.DatetimeIndex(n.snapshots)[0].year)
    apply_start_year = int(guard_cfg.get("apply_start_year", 2025))
    apply_end_year = int(guard_cfg.get("apply_end_year", 2060))
    if planning_year < apply_start_year or planning_year > apply_end_year:
        logger.info(
            "Solar capacity guard: year %s outside [%s, %s], skip.",
            planning_year,
            apply_start_year,
            apply_end_year,
        )
        return
    csv_path = Path(
        str(guard_cfg.get("national_capacity_csv", "data/p_nom/national_solar_capacity_from_planning_nc.csv"))
    )
    if not csv_path.is_absolute():
        csv_path = Path(__file__).resolve().parents[1] / csv_path
    if not csv_path.exists():
        logger.warning("Solar capacity guard enabled but file not found: %s", csv_path)
        return

    targets = pd.read_csv(csv_path)
    if "year" not in targets.columns or "national_solar_capacity_mw" not in targets.columns:
        logger.warning("Solar capacity guard CSV missing required columns: %s", csv_path)
        return
    targets = targets.set_index("year")
    if planning_year not in targets.index:
        logger.info("Solar capacity guard: no national target for year %s; skip.", planning_year)
        return

    use_historical_2025_baseline = bool(guard_cfg.get("use_historical_2025_baseline", True))
    if planning_year == 2025 and use_historical_2025_baseline:
        national_target_mw = _get_historical_2025_baseline_mw(config, guard_cfg)
    else:
        national_target_mw = float(targets.at[planning_year, "national_solar_capacity_mw"])
    if national_target_mw <= 0:
        logger.warning("Solar capacity guard: non-positive national target for %s; skip.", planning_year)
        return

    try:
        shares = _get_historical_solar_shares(config, guard_cfg)
    except Exception as e:
        logger.warning("Solar capacity guard: failed to get historical provincial shares: %s", e)
        return

    tol = float(guard_cfg.get("tolerance", 0.2))
    # If enabled, apply one-sided cap only:
    # can underbuild, cannot overbuild allocated target.
    allow_underbuild_only = bool(guard_cfg.get("allow_underbuild_only", True))
    if allow_underbuild_only:
        lower_mult = 0.0
        upper_mult = 1.0
    else:
        lower_mult = max(0.0, 1.0 - tol)
        upper_mult = 1.0 + tol

    solar = n.generators[n.generators.carrier == "solar"]
    ext = solar[solar.p_nom_extendable]
    if ext.empty:
        logger.info("Solar capacity guard: no extendable solar generators; skip.")
        return

    fixed = solar[~solar.p_nom_extendable]
    fixed_by_bus = fixed.groupby("bus")["p_nom"].sum() if not fixed.empty else pd.Series(dtype=float)

    updated = 0
    relaxed_min_count = 0
    for bus, ext_i in ext.groupby("bus").groups.items():
        if bus not in shares.index:
            continue

        target_total = national_target_mw * float(shares.loc[bus])
        min_total = target_total * lower_mult
        max_total = target_total * upper_mult

        fixed_cap = float(fixed_by_bus.get(bus, 0.0))
        current_max = n.generators.loc[ext_i, "p_nom_max"].clip(lower=0.0)
        potential_ext = float(current_max.sum())

        min_ext = max(min_total - fixed_cap, 0.0)
        max_ext = max(max_total - fixed_cap, 0.0)

        if min_ext > potential_ext + 1e-6:
            min_ext_applied = 0.0
            max_ext_applied = potential_ext
            relaxed_min_count += 1
        else:
            max_ext_applied = min(max_ext, potential_ext)
            min_ext_applied = min(min_ext, max_ext_applied)

        if potential_ext <= 0:
            continue

        weights = current_max / potential_ext
        if not np.isfinite(weights).all() or float(weights.sum()) <= 0:
            weights = pd.Series(1.0 / len(ext_i), index=ext_i)

        n.generators.loc[ext_i, "p_nom_min"] = min_ext_applied * weights.values
        n.generators.loc[ext_i, "p_nom_max"] = max_ext_applied * weights.values
        n.generators.loc[ext_i, "p_nom_max"] = np.maximum(
            n.generators.loc[ext_i, "p_nom_max"], n.generators.loc[ext_i, "p_nom_min"]
        )
        updated += len(ext_i)

    logger.info(
        "Solar capacity guard applied for %s: national_target=%.2f MW, updated_generators=%s, relaxed_min_buses=%s",
        planning_year,
        national_target_mw,
        updated,
        relaxed_min_count,
    )
