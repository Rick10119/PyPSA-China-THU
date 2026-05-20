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
            "data/existing_infrastructure/battery capacity.csv",
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


def apply_storage_capacity_guard(n, config, scenario_context: dict | None = None):
    """
    Cap *new* battery (new-type storage) build in each myopic planning step.

    Rule (per province):
        new battery power in planning year Y <= provincial 2025 baseline power

    National cumulative targets in `national_capacity_csv` (NEA / 储能产业研究白皮书2026)
    are logged for traceability only.
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

    try:
        baseline_by_province = _get_provincial_2025_baseline_mw(guard_cfg)
    except Exception as e:
        logger.warning("Storage capacity guard: failed to load provincial baseline: %s", e)
        return

    multiplier = float(guard_cfg.get("new_build_cap_multiplier", 1.0))
    allow_underbuild_only = bool(guard_cfg.get("allow_underbuild_only", True))
    max_hours = float(
        config.get("electricity", {}).get("max_hours", {}).get("battery", 6.0)
    )

    national_cumulative_target = _get_national_cumulative_target_mw(guard_cfg, planning_year)
    national_baseline_2025 = float(baseline_by_province.sum())

    updated_links = 0
    updated_stores = 0

    if hasattr(n, "links") and not n.links.empty:
        battery_links = n.links[n.links.carrier.astype(str) == "battery"]
        for idx, row in battery_links.iterrows():
            if not bool(row.get("p_nom_extendable", False)):
                continue
            if not _is_new_build_for_year(idx, int(row.get("build_year", 0)), planning_year):
                continue

            province = _province_from_battery_index(str(idx))
            cap_power = float(baseline_by_province.get(province, 0.0)) * multiplier
            current_max = float(row.get("p_nom_max", np.inf))
            if not np.isfinite(current_max):
                current_max = np.inf
            new_max = min(current_max, cap_power)
            new_min = 0.0 if allow_underbuild_only else float(row.get("p_nom_min", 0.0))
            new_min = min(new_min, new_max)

            n.links.at[idx, "p_nom_max"] = new_max
            n.links.at[idx, "p_nom_min"] = new_min
            updated_links += 1

    if hasattr(n, "stores") and not n.stores.empty:
        battery_stores = n.stores[n.stores.carrier.astype(str) == "battery"]
        for idx, row in battery_stores.iterrows():
            if not bool(row.get("e_nom_extendable", False)):
                continue
            if not _is_new_build_for_year(idx, int(row.get("build_year", 0)), planning_year):
                continue

            province = _province_from_battery_index(str(idx))
            cap_power = float(baseline_by_province.get(province, 0.0)) * multiplier
            cap_energy = cap_power * max_hours
            current_max = float(row.get("e_nom_max", np.inf))
            if not np.isfinite(current_max):
                current_max = np.inf
            new_max = min(current_max, cap_energy)
            new_min = 0.0 if allow_underbuild_only else float(row.get("e_nom_min", 0.0))
            new_min = min(new_min, new_max)

            n.stores.at[idx, "e_nom_max"] = new_max
            n.stores.at[idx, "e_nom_min"] = new_min
            updated_stores += 1

    logger.info(
        "Storage capacity guard applied for %s: updated_links=%s, updated_stores=%s, "
        "national_new_cap_mw=%.2f (sum of provincial 2025 baselines × %.3f), "
        "national_cumulative_target_mw=%s",
        planning_year,
        updated_links,
        updated_stores,
        national_baseline_2025 * multiplier,
        multiplier,
        f"{national_cumulative_target:.2f}" if national_cumulative_target is not None else "n/a",
    )

    if updated_links == 0 and updated_stores == 0:
        logger.info(
            "Storage capacity guard: no extendable new-build battery assets for %s.",
            planning_year,
        )
