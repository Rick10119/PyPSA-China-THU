import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _resolve_path(path_str: str) -> Path:
    p = Path(str(path_str))
    if not p.is_absolute():
        p = Path(__file__).resolve().parents[1] / p
    return p


def _get_historical_shares(csv_path: Path, year_cols: list[str]) -> pd.Series:
    if not csv_path.exists():
        raise FileNotFoundError(f"Historical wind capacity file not found: {csv_path}")

    head = pd.read_csv(csv_path, nrows=1)
    if "State/Province" not in head.columns:
        raise ValueError(f"'State/Province' column missing in {csv_path}")

    df = pd.read_csv(csv_path)
    missing = [c for c in year_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Missing historical year columns {missing} in {csv_path}")

    df = df.copy()
    df[year_cols] = df[year_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    by_province = df.groupby("State/Province")[year_cols].sum().sum(axis=1)
    total = float(by_province.sum())
    if total <= 0:
        raise ValueError(f"Historical total wind capacity is non-positive in {csv_path}")
    return by_province / total


def _apply_single_carrier_guard(
    n,
    carrier: str,
    national_target_mw: float,
    shares: pd.Series,
    allow_underbuild_only: bool,
    tolerance: float,
    target_lower_multiplier: float | None = None,
    target_upper_multiplier: float | None = None,
):
    if target_lower_multiplier is not None and target_upper_multiplier is not None:
        lower_mult = max(0.0, float(target_lower_multiplier))
        upper_mult = max(lower_mult, float(target_upper_multiplier))
    else:
        lower_mult = 0.0 if allow_underbuild_only else max(0.0, 1.0 - tolerance)
        upper_mult = 1.0 if allow_underbuild_only else 1.0 + tolerance

    gens = n.generators[n.generators.carrier == carrier]
    ext = gens[gens.p_nom_extendable]
    if ext.empty:
        logger.info("Wind capacity guard: no extendable %s generators; skip.", carrier)
        return 0, 0

    fixed = gens[~gens.p_nom_extendable]
    fixed_by_bus = fixed.groupby("bus")["p_nom"].sum() if not fixed.empty else pd.Series(dtype=float)

    updated = 0
    relaxed = 0
    for bus, ext_i in ext.groupby("bus").groups.items():
        if bus not in shares.index:
            # Province has no historical share for this carrier -> no new build.
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
            relaxed += 1
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

    return updated, relaxed


def apply_wind_capacity_guard(n, config):
    """
    Apply provincial upper bounds for onshore/offshore wind from national targets.

    Targets CSV columns:
    - year
    - national_onwind_capacity_mw
    - national_offwind_capacity_mw
    """
    guard_cfg = config.get("wind_capacity_guard", {})
    if not bool(guard_cfg.get("enabled", False)):
        return

    planning_year = int(pd.DatetimeIndex(n.snapshots)[0].year)
    apply_start_year = int(guard_cfg.get("apply_start_year", 2025))
    apply_end_year = int(guard_cfg.get("apply_end_year", 2060))
    if planning_year < apply_start_year or planning_year > apply_end_year:
        logger.info(
            "Wind capacity guard: year %s outside [%s, %s], skip.",
            planning_year,
            apply_start_year,
            apply_end_year,
        )
        return

    csv_path = _resolve_path(
        guard_cfg.get(
            "national_capacity_csv",
            "data/p_nom/national_wind_capacity_from_planning.csv",
        )
    )
    if not csv_path.exists():
        logger.warning("Wind capacity guard enabled but file not found: %s", csv_path)
        return

    targets = pd.read_csv(csv_path)
    required = {"year", "national_onwind_capacity_mw", "national_offwind_capacity_mw"}
    if not required.issubset(targets.columns):
        logger.warning("Wind capacity guard CSV missing required columns: %s", csv_path)
        return
    targets = targets.set_index("year")
    if planning_year not in targets.index:
        logger.info("Wind capacity guard: no national target for year %s; skip.", planning_year)
        return

    onwind_target = float(targets.at[planning_year, "national_onwind_capacity_mw"])
    offwind_target = float(targets.at[planning_year, "national_offwind_capacity_mw"])
    if onwind_target <= 0 and offwind_target <= 0:
        logger.warning("Wind capacity guard: non-positive targets for %s; skip.", planning_year)
        return

    onwind_hist_path = _resolve_path(
        guard_cfg.get("onwind_historical_capacity_csv", "data/existing_infrastructure/onwind_capacity.csv")
    )
    offwind_hist_path = _resolve_path(
        guard_cfg.get("offwind_historical_capacity_csv", "data/existing_infrastructure/offwind_capacity.csv")
    )
    onwind_year_cols = guard_cfg.get("onwind_historical_year_columns", ["2010", "2015", "2020", "2025"])
    offwind_year_cols = guard_cfg.get("offwind_historical_year_columns", ["2010", "2015", "2020", "2025"])

    try:
        onwind_shares = _get_historical_shares(onwind_hist_path, onwind_year_cols)
        offwind_shares = _get_historical_shares(offwind_hist_path, offwind_year_cols)
    except Exception as e:
        logger.warning("Wind capacity guard: failed to get historical shares: %s", e)
        return

    allow_underbuild_only = bool(guard_cfg.get("allow_underbuild_only", True))
    tolerance = float(guard_cfg.get("tolerance", 0.2))
    lower_mult_cfg = guard_cfg.get("target_lower_multiplier")
    upper_mult_cfg = guard_cfg.get("target_upper_multiplier")
    lower_mult = float(lower_mult_cfg) if lower_mult_cfg is not None else None
    upper_mult = float(upper_mult_cfg) if upper_mult_cfg is not None else None

    on_u, on_r = _apply_single_carrier_guard(
        n,
        carrier="onwind",
        national_target_mw=onwind_target,
        shares=onwind_shares,
        allow_underbuild_only=allow_underbuild_only,
        tolerance=tolerance,
        target_lower_multiplier=lower_mult,
        target_upper_multiplier=upper_mult,
    )
    off_u, off_r = _apply_single_carrier_guard(
        n,
        carrier="offwind",
        national_target_mw=offwind_target,
        shares=offwind_shares,
        allow_underbuild_only=allow_underbuild_only,
        tolerance=tolerance,
        target_lower_multiplier=lower_mult,
        target_upper_multiplier=upper_mult,
    )

    logger.info(
        "Wind capacity guard applied for %s: onwind_target=%.2f MW (updated=%s, relaxed=%s), "
        "offwind_target=%.2f MW (updated=%s, relaxed=%s), lower_mult=%s, upper_mult=%s",
        planning_year,
        onwind_target,
        on_u,
        on_r,
        offwind_target,
        off_u,
        off_r,
        lower_mult if lower_mult is not None else ("0.0" if allow_underbuild_only else f"{max(0.0, 1.0 - tolerance):.3f}"),
        upper_mult if upper_mult is not None else ("1.0" if allow_underbuild_only else f"{1.0 + tolerance:.3f}"),
    )
