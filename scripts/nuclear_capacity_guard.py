import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _get_historical_nuclear_shares(guard_cfg: dict) -> pd.Series:
    """
    Get provincial share vector from historical installed nuclear capacity table.
    Shares are based on sum(historical_year_columns) by province.
    """
    hist_path = Path(
        str(guard_cfg.get("historical_capacity_csv", "data/existing_infrastructure/nuclear capacity.csv"))
    )
    if not hist_path.is_absolute():
        hist_path = Path(__file__).resolve().parents[1] / hist_path
    if not hist_path.exists():
        raise FileNotFoundError(f"Historical nuclear capacity file not found: {hist_path}")

    year_cols = guard_cfg.get("historical_year_columns", ["2020", "2025"])
    if "Province" not in pd.read_csv(hist_path, nrows=1).columns:
        raise ValueError(f"'Province' column missing in {hist_path}")

    df = pd.read_csv(hist_path)
    missing = [c for c in year_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing historical year columns {missing} in {hist_path}")

    df = df.copy()
    df[year_cols] = df[year_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    by_region = df.groupby("Province")[year_cols].sum().sum(axis=1)
    total = float(by_region.sum())
    if total <= 0:
        raise ValueError(f"Historical total nuclear capacity is non-positive in {hist_path}")
    shares = by_region / total
    return shares


def apply_nuclear_capacity_guard(n, config):
    """
    Apply provincial upper bounds for nuclear capacity from national targets.

    - Reads national targets from data/p_nom/national_nuclear_capacity_mid_scenario.csv
    - Allocates by 2025 provincial shares (from historical table)
    - Applies one-sided cap by default: allow underbuild, forbid overbuild
    """
    guard_cfg = config.get("nuclear_capacity_guard", {})
    if not bool(guard_cfg.get("enabled", False)):
        return

    planning_year = int(pd.DatetimeIndex(n.snapshots)[0].year)
    apply_start_year = int(guard_cfg.get("apply_start_year", 2030))
    apply_end_year = int(guard_cfg.get("apply_end_year", 2060))
    if planning_year < apply_start_year or planning_year > apply_end_year:
        logger.info(
            "Nuclear capacity guard: year %s outside [%s, %s], skip.",
            planning_year,
            apply_start_year,
            apply_end_year,
        )
        return

    csv_path = Path(
        str(
            guard_cfg.get(
                "national_capacity_csv",
                "data/p_nom/national_nuclear_capacity_mid_scenario.csv",
            )
        )
    )
    if not csv_path.is_absolute():
        csv_path = Path(__file__).resolve().parents[1] / csv_path
    if not csv_path.exists():
        logger.warning("Nuclear capacity guard enabled but file not found: %s", csv_path)
        return

    targets = pd.read_csv(csv_path)
    if "year" not in targets.columns or "national_nuclear_capacity_mw" not in targets.columns:
        logger.warning("Nuclear capacity guard CSV missing required columns: %s", csv_path)
        return
    targets = targets.set_index("year")
    if planning_year not in targets.index:
        logger.info("Nuclear capacity guard: no national target for year %s; skip.", planning_year)
        return

    national_target_mw = float(targets.at[planning_year, "national_nuclear_capacity_mw"])
    if national_target_mw <= 0:
        logger.warning("Nuclear capacity guard: non-positive national target for %s; skip.", planning_year)
        return

    try:
        shares = _get_historical_nuclear_shares(guard_cfg)
    except Exception as e:
        logger.warning("Nuclear capacity guard: failed to get historical provincial shares: %s", e)
        return

    tol = float(guard_cfg.get("tolerance", 0.2))
    allow_underbuild_only = bool(guard_cfg.get("allow_underbuild_only", True))
    if allow_underbuild_only:
        lower_mult = 0.0
        upper_mult = 1.0
    else:
        lower_mult = max(0.0, 1.0 - tol)
        upper_mult = 1.0 + tol

    nuclear = n.generators[n.generators.carrier == "nuclear"]
    ext = nuclear[nuclear.p_nom_extendable]
    if ext.empty:
        logger.info("Nuclear capacity guard: no extendable nuclear generators; skip.")
        return

    fixed = nuclear[~nuclear.p_nom_extendable]
    fixed_by_bus = fixed.groupby("bus")["p_nom"].sum() if not fixed.empty else pd.Series(dtype=float)

    updated = 0
    relaxed_min_count = 0
    for bus, ext_i in ext.groupby("bus").groups.items():
        if bus not in shares.index:
            # If province has zero historical share, force no expansion.
            n.generators.loc[ext_i, "p_nom_min"] = 0.0
            n.generators.loc[ext_i, "p_nom_max"] = 0.0
            updated += len(ext_i)
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
        "Nuclear capacity guard applied for %s: national_target=%.2f MW, updated_generators=%s, relaxed_min_buses=%s",
        planning_year,
        national_target_mw,
        updated,
        relaxed_min_count,
    )
