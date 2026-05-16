import numpy as np
import pandas as pd


_DEFAULT_RENEWABLE_GENERATOR_CARRIERS = {
    "onwind",
    "offwind",
    "solar",
    "solar thermal",
    "hydroelectricity",
    "hydro_inflow",
    "biomass",
}

_DEFAULT_NUCLEAR_GENERATOR_CARRIERS = {"nuclear"}


def _pin_generator_nominal_to_minimum_and_lock(n, carriers):
    if not hasattr(n, "generators") or n.generators.empty:
        return
    mask = n.generators.carrier.astype(str).isin(set(carriers))
    idx = n.generators.index[mask]
    if not len(idx):
        return

    p_nom = pd.to_numeric(n.generators.loc[idx, "p_nom"], errors="coerce").fillna(0.0)
    p_nom_min = pd.to_numeric(n.generators.loc[idx, "p_nom_min"], errors="coerce").fillna(0.0)
    n.generators.loc[idx, "p_nom"] = np.maximum(p_nom, p_nom_min)
    n.generators.loc[idx, "p_nom_extendable"] = False


def _pin_battery_nominal_to_minimum_and_lock(n):
    if hasattr(n, "stores") and not n.stores.empty:
        mask = n.stores.carrier.astype(str) == "battery"
        idx = n.stores.index[mask]
        if len(idx):
            e_nom = pd.to_numeric(n.stores.loc[idx, "e_nom"], errors="coerce").fillna(0.0)
            e_nom_min = pd.to_numeric(n.stores.loc[idx, "e_nom_min"], errors="coerce").fillna(0.0)
            n.stores.loc[idx, "e_nom"] = np.maximum(e_nom, e_nom_min)
            n.stores.loc[idx, "e_nom_extendable"] = False

    if hasattr(n, "links") and not n.links.empty:
        mask = n.links.carrier.astype(str) == "battery"
        idx = n.links.index[mask]
        if len(idx):
            p_nom = pd.to_numeric(n.links.loc[idx, "p_nom"], errors="coerce").fillna(0.0)
            p_nom_min = pd.to_numeric(n.links.loc[idx, "p_nom_min"], errors="coerce").fillna(0.0)
            n.links.loc[idx, "p_nom"] = np.maximum(p_nom, p_nom_min)
            n.links.loc[idx, "p_nom_extendable"] = False

    if hasattr(n, "storage_units") and not n.storage_units.empty:
        mask = n.storage_units.carrier.astype(str) == "battery"
        idx = n.storage_units.index[mask]
        if len(idx):
            p_nom = pd.to_numeric(n.storage_units.loc[idx, "p_nom"], errors="coerce").fillna(0.0)
            p_nom_min = pd.to_numeric(n.storage_units.loc[idx, "p_nom_min"], errors="coerce").fillna(0.0)
            n.storage_units.loc[idx, "p_nom"] = np.maximum(p_nom, p_nom_min)
            n.storage_units.loc[idx, "p_nom_extendable"] = False


def apply_baseyear_capacity_locks(n, planning_horizon, config=None):
    """
    Apply base-year (2025) no-expansion policy.

    Scope:
    - Battery (Store/Link/StorageUnit carrier == "battery")
    - Renewable generators
    - Nuclear generators
    """
    if str(planning_horizon) != "2025":
        return

    renewable_carriers = set(_DEFAULT_RENEWABLE_GENERATOR_CARRIERS)
    nuclear_carriers = set(_DEFAULT_NUCLEAR_GENERATOR_CARRIERS)

    if isinstance(config, dict):
        techs = config.get("Techs", {}) or {}
        vre_cfg = techs.get("vre_techs", []) or []
        if isinstance(vre_cfg, list):
            for c in vre_cfg:
                c_str = str(c)
                if c_str == "nuclear":
                    nuclear_carriers.add(c_str)
                else:
                    renewable_carriers.add(c_str)

    _pin_battery_nominal_to_minimum_and_lock(n)
    _pin_generator_nominal_to_minimum_and_lock(n, renewable_carriers)
    _pin_generator_nominal_to_minimum_and_lock(n, nuclear_carriers)
