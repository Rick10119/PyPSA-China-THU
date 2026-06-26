from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _resolve_repo_path(path_str: str) -> Path:
    path = Path(str(path_str))
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    return path


def _get_provincial_2025_baseline_mw(guard_cfg: dict) -> pd.Series:
    """
    Provincial 2025 battery power baseline [MW] from existing_infrastructure CSV.

    The CSV records power capacity (MW), aligned with NEA end-2025 new-type storage.
    """
    hist_path = _resolve_repo_path(
        guard_cfg.get(
            "historical_capacity_csv",
            "data/existing_infrastructure/battery_capacity.csv",
        )
    )
    if not hist_path.exists():
        raise FileNotFoundError(f"Historical battery capacity file not found: {hist_path}")

    baseline_col = str(guard_cfg.get("baseline_year_column", "2025"))
    df = pd.read_csv(hist_path)
    if "Region" not in df.columns:
        raise ValueError(f"'Region' column missing in {hist_path}")
    if baseline_col not in df.columns:
        raise ValueError(f"Baseline column '{baseline_col}' missing in {hist_path}")

    baseline = df.set_index("Region")[baseline_col]
    baseline = pd.to_numeric(baseline, errors="coerce").fillna(0.0)
    return baseline.astype(float)


def get_provincial_storage_shares(guard_cfg: dict) -> pd.Series:
    """Provincial storage target shares from the configured historical baseline year."""
    baseline = _get_provincial_2025_baseline_mw(guard_cfg)
    total = float(baseline.sum())
    if total <= 0:
        raise ValueError("Historical total battery capacity is non-positive.")
    return baseline / total


def _province_from_battery_index(name: str) -> str:
    for suffix in (" battery charger", " battery discharger", " battery"):
        if suffix in name:
            return name.split(suffix)[0]
    return name


def _is_new_build_for_year(name: str, build_year: int, planning_year: int) -> bool:
    if int(build_year) == int(planning_year):
        return True
    return bool(re.search(rf"-{planning_year}$", str(name)))


def _get_national_cumulative_target_mw(guard_cfg: dict, planning_year: int) -> float | None:
    csv_path = _resolve_repo_path(
        guard_cfg.get(
            "national_capacity_csv",
            "data/p_nom/national_battery_capacity_from_planning.csv",
        )
    )
    if not csv_path.exists():
        return None
    targets = pd.read_csv(csv_path)
    if "year" not in targets.columns or "national_battery_capacity_mw" not in targets.columns:
        return None
    targets = targets.set_index("year")
    if planning_year not in targets.index:
        return None
    return float(targets.at[planning_year, "national_battery_capacity_mw"])


def _target_multipliers(guard_cfg: dict) -> tuple[float, float]:
    allow_underbuild_only = bool(guard_cfg.get("allow_underbuild_only", True))
    tol = float(guard_cfg.get("tolerance", 0.2))
    lower_mult_cfg = guard_cfg.get("target_lower_multiplier")
    upper_mult_cfg = guard_cfg.get("target_upper_multiplier")
    if lower_mult_cfg is not None and upper_mult_cfg is not None:
        lower_mult = max(0.0, float(lower_mult_cfg))
        upper_mult = max(lower_mult, float(upper_mult_cfg))
    else:
        if allow_underbuild_only:
            lower_mult = 0.0
            upper_mult = 1.0 + tol
        else:
            lower_mult = max(0.0, 1.0 - tol)
            upper_mult = 1.0 + tol
    return lower_mult, upper_mult


def _target_capacity_multiplier(guard_cfg: dict) -> float:
    """Uniform multiplier for storage availability sensitivity cases."""
    return max(0.0, float(guard_cfg.get("target_capacity_multiplier", 1.0)))


def _fixed_battery_power_by_province(n) -> pd.Series:
    """Fixed/non-extendable battery discharger power by province [MW]."""
    if not hasattr(n, "links") or n.links.empty:
        return pd.Series(dtype=float)
    links = n.links[n.links.carrier.astype(str) == "battery"]
    if links.empty:
        return pd.Series(dtype=float)
    dischargers = links[links.index.astype(str).str.contains(" battery discharger")]
    if dischargers.empty:
        return pd.Series(dtype=float)
    fixed = dischargers[~dischargers.p_nom_extendable.astype(bool)]
    if fixed.empty:
        return pd.Series(dtype=float)
    provinces = fixed.index.to_series().map(_province_from_battery_index)
    return fixed["p_nom"].groupby(provinces).sum().astype(float)


def _set_bounds(df: pd.DataFrame, idx, min_col: str, max_col: str, min_total: float, max_total: float) -> None:
    current_max = pd.to_numeric(df.loc[idx, max_col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    finite_max = current_max.dropna().clip(lower=0.0)
    potential = float(finite_max.sum()) if len(finite_max) == len(idx) else np.inf
    if np.isfinite(potential):
        max_applied = min(float(max_total), potential)
    else:
        max_applied = float(max_total)
    min_applied = min(max(float(min_total), 0.0), max_applied)

    if np.isfinite(potential) and potential > 0:
        weights = finite_max / potential
    else:
        weights = pd.Series(1.0 / len(idx), index=idx)

    df.loc[idx, min_col] = min_applied * weights.values
    df.loc[idx, max_col] = max_applied * weights.values
    df.loc[idx, max_col] = np.maximum(df.loc[idx, max_col], df.loc[idx, min_col])


def apply_storage_capacity_guard(n, config, scenario_context: dict | None = None):
    """
    Apply provincial battery power/energy bounds from national cumulative targets.

    National battery targets are allocated by provincial historical storage shares
    and converted to extendable power/energy bounds after subtracting fixed stock.
    """
    guard_cfg = config.get("storage_capacity_guard", {})
    if not bool(guard_cfg.get("enabled", False)):
        return

    planning_year = int(pd.DatetimeIndex(n.snapshots)[0].year)
    apply_start_year = int(guard_cfg.get("apply_start_year", 2030))
    apply_end_year = int(guard_cfg.get("apply_end_year", 2060))
    if planning_year < apply_start_year or planning_year > apply_end_year:
        logger.info(
            "Storage capacity guard: year %s outside [%s, %s], skip.",
            planning_year,
            apply_start_year,
            apply_end_year,
        )
        return

    national_cumulative_target = _get_national_cumulative_target_mw(guard_cfg, planning_year)
    if national_cumulative_target is None or national_cumulative_target <= 0:
        logger.info("Storage capacity guard: no positive national target for %s; skip.", planning_year)
        return
    capacity_multiplier = _target_capacity_multiplier(guard_cfg)
    national_cumulative_target *= capacity_multiplier

    try:
        shares = get_provincial_storage_shares(guard_cfg)
    except Exception as e:
        logger.warning("Storage capacity guard: failed to load provincial shares: %s", e)
        return

    lower_mult, upper_mult = _target_multipliers(guard_cfg)
    max_hours = float(
        config.get("electricity", {}).get("max_hours", {}).get("battery", 6.0)
    )

    fixed_power_by_province = _fixed_battery_power_by_province(n)
    target_power_by_province = shares * float(national_cumulative_target)

    updated_links = 0
    updated_stores = 0

    if hasattr(n, "links") and not n.links.empty:
        battery_links = n.links[n.links.carrier.astype(str) == "battery"]
        if not battery_links.empty:
            is_ext = battery_links.p_nom_extendable.astype(bool)
            is_new = pd.Series(
                [
                    _is_new_build_for_year(idx, int(row.get("build_year", 0)), planning_year)
                    for idx, row in battery_links.iterrows()
                ],
                index=battery_links.index,
            )
            ext_new = battery_links[is_ext & is_new]
            for (province, suffix), ext_i in ext_new.groupby(
                [
                    ext_new.index.to_series().map(_province_from_battery_index),
                    ext_new.index.to_series().map(
                        lambda x: "discharger" if " battery discharger" in str(x) else "charger"
                    ),
                ]
            ).groups.items():
                target_power = float(target_power_by_province.get(province, 0.0))
                fixed_power = float(fixed_power_by_province.get(province, 0.0))
                min_power = max(target_power * lower_mult - fixed_power, 0.0)
                max_power = max(target_power * upper_mult - fixed_power, 0.0)
                _set_bounds(n.links, ext_i, "p_nom_min", "p_nom_max", min_power, max_power)
                updated_links += len(ext_i)

    if hasattr(n, "stores") and not n.stores.empty:
        battery_stores = n.stores[n.stores.carrier.astype(str) == "battery"]
        if not battery_stores.empty:
            is_ext = battery_stores.e_nom_extendable.astype(bool)
            is_new = pd.Series(
                [
                    _is_new_build_for_year(idx, int(row.get("build_year", 0)), planning_year)
                    for idx, row in battery_stores.iterrows()
                ],
                index=battery_stores.index,
            )
            ext_new = battery_stores[is_ext & is_new]
            for province, ext_i in ext_new.groupby(ext_new.index.to_series().map(_province_from_battery_index)).groups.items():
                target_power = float(target_power_by_province.get(province, 0.0))
                fixed_power = float(fixed_power_by_province.get(province, 0.0))
                min_energy = max(target_power * lower_mult - fixed_power, 0.0) * max_hours
                max_energy = max(target_power * upper_mult - fixed_power, 0.0) * max_hours
                _set_bounds(n.stores, ext_i, "e_nom_min", "e_nom_max", min_energy, max_energy)
                updated_stores += len(ext_i)

    logger.info(
        "Storage capacity guard applied for %s: updated_links=%s, updated_stores=%s, "
        "national_cumulative_target_mw=%.2f, target_capacity_multiplier=%.3f, "
        "lower_mult=%.3f, upper_mult=%.3f, max_hours=%.2f",
        planning_year,
        updated_links,
        updated_stores,
        national_cumulative_target,
        capacity_multiplier,
        lower_mult,
        upper_mult,
        max_hours,
    )

    if updated_links == 0 and updated_stores == 0:
        logger.info(
            "Storage capacity guard: no extendable new-build battery assets for %s.",
            planning_year,
        )
