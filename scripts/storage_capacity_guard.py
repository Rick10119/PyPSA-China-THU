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


def _yearly_min_capacity_mw(guard_cfg: dict, planning_year: int) -> float:
    """Optional absolute national cumulative lower target by planning year [MW]."""
    values = guard_cfg.get("national_min_capacity_mw_by_year") or {}
    if not isinstance(values, dict):
        return 0.0
    value = values.get(planning_year, values.get(str(planning_year), 0.0))
    return max(0.0, float(value or 0.0))


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


def _fixed_battery_power_mw(n) -> float:
    """Fixed/non-extendable battery discharger power nationally [MW]."""
    return float(_fixed_battery_power_by_province(n).sum())


def _battery_max_hours(config) -> float:
    return float(config.get("electricity", {}).get("max_hours", {}).get("battery", 6.0))


def _safe_constraint_suffix(name: str) -> str:
    return re.sub(r"[^\w]+", "-", str(name)).strip("-") or "unknown"


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


def _storage_guard_limits(config, planning_year: int) -> tuple[float, float, float, float, float, float] | None:
    guard_cfg = config.get("storage_capacity_guard", {})
    if not bool(guard_cfg.get("enabled", False)):
        return None

    apply_start_year = int(guard_cfg.get("apply_start_year", 2030))
    apply_end_year = int(guard_cfg.get("apply_end_year", 2060))
    if planning_year < apply_start_year or planning_year > apply_end_year:
        return None

    national_cumulative_target_raw = _get_national_cumulative_target_mw(guard_cfg, planning_year)
    if national_cumulative_target_raw is None or national_cumulative_target_raw <= 0:
        logger.info("Storage capacity guard: no positive national target for %s; skip.", planning_year)
        return None
    capacity_multiplier = _target_capacity_multiplier(guard_cfg)
    national_cumulative_target = national_cumulative_target_raw * capacity_multiplier

    lower_mult, upper_mult = _target_multipliers(guard_cfg)
    min_cumulative_target = max(
        national_cumulative_target * lower_mult,
        _yearly_min_capacity_mw(guard_cfg, planning_year),
    )
    max_cumulative_target = national_cumulative_target * upper_mult
    if min_cumulative_target > max_cumulative_target:
        logger.warning(
            "Storage capacity guard: min target %.2f MW exceeds max target %.2f MW for %s; "
            "clipping min to max to avoid infeasibility.",
            min_cumulative_target,
            max_cumulative_target,
            planning_year,
        )
        min_cumulative_target = max_cumulative_target
    max_hours = _battery_max_hours(config)
    return national_cumulative_target, min_cumulative_target, max_cumulative_target, lower_mult, upper_mult, capacity_multiplier, max_hours


def apply_storage_capacity_guard(n, config, scenario_context: dict | None = None):
    """
    Keep storage capacity guard setup out of per-province bounds.

    The actual national-only cap is added as a model constraint in
    add_storage_capacity_guard_constraints(). This function intentionally does
    not modify per-asset p_nom_max/e_nom_max, so provincial siting remains free.
    """
    planning_year = int(pd.DatetimeIndex(n.snapshots)[0].year)
    limits = _storage_guard_limits(config, planning_year)
    if limits is None:
        return

    national_cumulative_target, min_cumulative_target, max_cumulative_target, lower_mult, upper_mult, capacity_multiplier, max_hours = limits
    logger.info(
        "Storage capacity guard prepared for %s as national-only cap: "
        "national_cumulative_target_mw=%.2f, min_cumulative_target_mw=%.2f, "
        "max_cumulative_target_mw=%.2f, target_capacity_multiplier=%.3f, "
        "lower_mult=%.3f, upper_mult=%.3f, max_hours=%.2f",
        planning_year,
        national_cumulative_target,
        min_cumulative_target,
        max_cumulative_target,
        capacity_multiplier,
        lower_mult,
        upper_mult,
        max_hours,
    )


def add_battery_max_hours_constraints(n, snapshots, config=None) -> None:
    """
    Couple each province's battery energy capacity to its discharger power.

    Enforces store e_nom <= max_hours * discharger p_nom so optimized duration
    cannot exceed electricity.max_hours.battery.
    """
    cfg = config if isinstance(config, dict) else getattr(n, "config", {}) or {}
    max_hours = _battery_max_hours(cfg)
    if max_hours <= 0:
        return

    if not hasattr(n, "stores") or n.stores.empty or not hasattr(n, "links") or n.links.empty:
        return

    battery_stores = n.stores[n.stores.carrier.astype(str) == "battery"]
    if battery_stores.empty:
        return

    battery_links = n.links[n.links.carrier.astype(str) == "battery"]
    dischargers = battery_links[battery_links.index.astype(str).str.contains(" battery discharger")]
    if dischargers.empty:
        return

    link_p_nom = n.model["Link-p_nom"] if "Link-p_nom" in n.model.variables else None
    store_e_nom = n.model["Store-e_nom"] if "Store-e_nom" in n.model.variables else None
    if link_p_nom is None or store_e_nom is None:
        logger.warning("Battery max-hours: optimization variables missing; skip.")
        return

    discharger_by_province = {
        _province_from_battery_index(str(idx)): idx for idx in dischargers.index
    }

    added = 0
    skipped = 0
    for store_idx in battery_stores.index:
        province = _province_from_battery_index(str(store_idx))
        dis_idx = discharger_by_province.get(province)
        if dis_idx is None:
            skipped += 1
            continue
        try:
            lhs = store_e_nom.loc[store_idx]
            rhs = max_hours * link_p_nom.loc[dis_idx]
        except (KeyError, ValueError):
            skipped += 1
            continue

        n.model.add_constraints(
            lhs <= rhs,
            name=f"battery-max-hours-{_safe_constraint_suffix(province)}",
        )
        added += 1

    logger.info(
        "Battery max-hours constraints for %s: added=%s, skipped=%s, max_hours=%.2f",
        int(pd.DatetimeIndex(snapshots)[0].year),
        added,
        skipped,
        max_hours,
    )


def add_storage_capacity_guard_constraints(n, snapshots, config=None) -> None:
    """Add national battery power/energy cap constraints without provincial allocation."""
    cfg = config if isinstance(config, dict) else getattr(n, "config", {}) or {}
    planning_year = int(pd.DatetimeIndex(snapshots)[0].year)
    limits = _storage_guard_limits(cfg, planning_year)
    if limits is None:
        return

    national_cumulative_target, min_cumulative_target, max_cumulative_target, lower_mult, upper_mult, capacity_multiplier, max_hours = limits

    fixed_power = _fixed_battery_power_mw(n)
    min_power = max(float(min_cumulative_target) - fixed_power, 0.0)
    max_power = max(float(max_cumulative_target) - fixed_power, 0.0)
    min_energy = min_power * max_hours
    max_energy = max_power * max_hours
    added = 0

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
            suffixes = ext_new.index.to_series().map(
                lambda x: "discharger" if " battery discharger" in str(x) else "charger"
            )
            link_p_nom = n.model["Link-p_nom"] if "Link-p_nom" in n.model.variables else None
            for suffix, ext_i in ext_new.groupby(suffixes).groups.items():
                if link_p_nom is None or len(ext_i) == 0:
                    continue
                lhs = link_p_nom.loc[ext_i].sum()
                n.model.add_constraints(lhs <= max_power, name=f"battery-national-{suffix}-p-nom-max")
                if min_power > 0:
                    n.model.add_constraints(lhs >= min_power, name=f"battery-national-{suffix}-p-nom-min")
                added += 1

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
            store_e_nom = n.model["Store-e_nom"] if "Store-e_nom" in n.model.variables else None
            if store_e_nom is not None and not ext_new.empty:
                lhs = store_e_nom.loc[ext_new.index].sum()
                n.model.add_constraints(lhs <= max_energy, name="battery-national-store-e-nom-max")
                if min_energy > 0:
                    n.model.add_constraints(lhs >= min_energy, name="battery-national-store-e-nom-min")
                added += 1

    logger.info(
        "Storage capacity guard national constraints for %s: added=%s, "
        "national_cumulative_target_mw=%.2f, min_cumulative_target_mw=%.2f, "
        "max_cumulative_target_mw=%.2f, target_capacity_multiplier=%.3f, "
        "lower_mult=%.3f, upper_mult=%.3f, fixed_power_mw=%.2f, max_hours=%.2f",
        planning_year,
        added,
        national_cumulative_target,
        min_cumulative_target,
        max_cumulative_target,
        capacity_multiplier,
        lower_mult,
        upper_mult,
        fixed_power,
        max_hours,
    )
