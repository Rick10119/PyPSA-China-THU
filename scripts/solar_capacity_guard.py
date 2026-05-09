import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

logger = logging.getLogger(__name__)


def _scenario_value(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def _get_solar_reference_shares(config, planning_year: int, scenario_context: dict | None = None) -> pd.Series:
    """
    Get provincial solar-capacity shares from the reference year network (default 2025).
    """
    cfg = config.get("solar_capacity_guard", {})
    ref_year = int(cfg.get("reference_year", 2025))
    repo_root = Path(__file__).resolve().parents[1]

    results_dir = str(config.get("results_dir", "results/")).strip()
    version = str(config.get("version", "")).strip()
    scenario = config.get("scenario", {})
    context = scenario_context or {}

    opts = str(context.get("opts", _scenario_value(scenario.get("opts"))))
    topology = str(context.get("topology", _scenario_value(scenario.get("topology"))))
    pathway = str(context.get("pathway", _scenario_value(scenario.get("pathway"))))
    heating_demand = str(context.get("heating_demand", _scenario_value(scenario.get("heating_demand"))))

    ref_path = (
        repo_root
        / results_dir
        / f"version-{version}"
        / "postnetworks"
        / heating_demand
        / f"postnetwork-{opts}-{topology}-{pathway}-{ref_year}.nc"
    )

    if not ref_path.exists():
        raise FileNotFoundError(
            f"Reference network for provincial share not found: {ref_path} "
            f"(planning year {planning_year})"
        )

    n_ref = pypsa.Network(ref_path)
    solar_ref = n_ref.generators[n_ref.generators.carrier == "solar"]
    if solar_ref.empty:
        raise ValueError(f"No solar generators in reference network: {ref_path}")

    cap_col = "p_nom_opt" if "p_nom_opt" in solar_ref.columns else "p_nom"
    cap_by_bus = solar_ref.groupby("bus")[cap_col].sum()
    total = float(cap_by_bus.sum())
    if total <= 0:
        raise ValueError(f"Reference-year solar capacity is non-positive: {ref_path}")
    return cap_by_bus / total


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

    national_target_mw = float(targets.at[planning_year, "national_solar_capacity_mw"])
    if national_target_mw <= 0:
        logger.warning("Solar capacity guard: non-positive national target for %s; skip.", planning_year)
        return

    try:
        shares = _get_solar_reference_shares(config, planning_year, scenario_context=scenario_context)
    except Exception as e:
        logger.warning("Solar capacity guard: failed to get provincial shares: %s", e)
        return

    tol = float(guard_cfg.get("tolerance", 0.2))
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
