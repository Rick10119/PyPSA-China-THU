"""
Utilities for a heat-only (electricity-price exogenous) solve.

The repo's base networks (built by `scripts/prepare_base_network.py`) contain a full
electricity system plus a heating sector (and possibly other sectors).

For the heat-decoupled workflow we keep:
- heat buses and heat loads
- heat technologies (heat pumps, resistive heaters, boilers)
- thermal storage (water tanks)
- CHP links (both generator + boiler links) and their coupling constraints (added in
  `scripts/solve_network_myopic.py:add_chp_constraints`)
- the minimal supporting fuel infrastructure (fuel buses + fuel supply generators/stores)

Everything else in the electricity system (generation fleet, transmission expansion, etc.)
is removed and replaced by an exogenous-price settlement layer added at solve time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd
import pypsa


@dataclass(frozen=True)
class HeatOnlyKeepRules:
    """
    Declarative keep rules for stripping a full network to a heat-only network.
    """

    keep_link_carriers: tuple[str, ...] = (
        "heat pump",
        "resistive heater",
        "coal boiler",
        "gas boiler",
        "water tanks",
        "CHP gas",
        "CHP coal",
        # Note: biomass CHP in this repo is modeled as a single multi-output Link
        # feeding both electricity (bus1) and heat (bus2). Keep it if present.
        "biomass",
        "H2",
        "H2 CHP",
        "DAC",
        "Sabatier",
    )

    keep_generator_carriers: tuple[str, ...] = (
        # Heat-side generation
        "solar thermal",
        # Fuel supply generators in prepare_base_network.py
        "gas",
        "coal",
        # Optional
        "hydro_inflow",
        "hydroelectricity",
    )

    keep_store_carriers: tuple[str, ...] = (
        "water tanks",
        "gas",
        "biomass",
        "co2 atmosphere",
        "co2 stored",
        "H2",
    )

    keep_load_bus_suffixes: tuple[str, ...] = (" central heat", " decentral heat")


def _safe_mremove(n: pypsa.Network, component: str, names: Iterable[str]) -> None:
    names = list(names)
    if not names:
        return
    try:
        n.mremove(component, names)
    except Exception:
        # Some components (e.g. empty) may error under certain PyPSA versions
        existing = [x for x in names if x in n.df(component).index]
        if existing:
            n.mremove(component, existing)


def strip_power_system_keep_heat(
    n: pypsa.Network,
    rules: HeatOnlyKeepRules | None = None,
) -> pypsa.Network:
    """
    Strip a full PyPSA-China network down to a heat-only network.

    This function mutates and returns `n`.

    Strategy:
    - Keep all heat buses and the AC buses required by electric-to-heat links and CHP.
    - Keep a small set of link/generator/store types required for heat feasibility.
    - Drop all other generators/links/lines/storage units and their dependent buses.
    """

    rules = rules or HeatOnlyKeepRules()

    # --- Keep loads on heat buses only
    heat_bus_names = n.buses.index[n.buses.carrier == "heat"]
    keep_loads = n.loads.index[n.loads.bus.isin(heat_bus_names)]

    # Also keep any load that sits on a bus with explicit heat suffix (defensive).
    if len(rules.keep_load_bus_suffixes) > 0:
        suffix_mask = pd.Series(False, index=n.loads.index)
        for suf in rules.keep_load_bus_suffixes:
            suffix_mask |= n.loads.bus.astype(str).str.endswith(suf)
        keep_loads = keep_loads.union(n.loads.index[suffix_mask])

    drop_loads = n.loads.index.difference(keep_loads)
    _safe_mremove(n, "Load", drop_loads)

    # --- Keep links by carrier (plus name-based CHP pairing)
    keep_links = n.links.index[n.links.carrier.isin(rules.keep_link_carriers)]

    # Name-based keep for CHP links (constraint code searches by name)
    chp_name_mask = n.links.index.to_series().astype(str).str.contains("CHP")
    keep_links = keep_links.union(n.links.index[chp_name_mask])

    drop_links = n.links.index.difference(keep_links)
    _safe_mremove(n, "Link", drop_links)

    # --- Keep generators: solar thermal + fuel supply generators; remove all AC-side fleet
    keep_generators = n.generators.index[n.generators.carrier.isin(rules.keep_generator_carriers)]

    # Fuel supply generators are created as "<Province> gas fuel" or "<Province> coal fuel"
    fuel_name_mask = n.generators.index.to_series().astype(str).str.contains(r"\b(?:gas|coal)\s+fuel\b", regex=True)
    keep_generators = keep_generators.union(n.generators.index[fuel_name_mask])

    drop_generators = n.generators.index.difference(keep_generators)
    _safe_mremove(n, "Generator", drop_generators)

    # --- Keep stores: thermal storage + fuel/CO2 infrastructure if present
    keep_stores = n.stores.index[n.stores.carrier.isin(rules.keep_store_carriers)]
    drop_stores = n.stores.index.difference(keep_stores)
    _safe_mremove(n, "Store", drop_stores)

    # --- Remove lines and storage units entirely (electric system artifacts)
    if hasattr(n, "lines") and not n.lines.empty:
        _safe_mremove(n, "Line", n.lines.index)

    if hasattr(n, "storage_units") and not n.storage_units.empty:
        _safe_mremove(n, "StorageUnit", n.storage_units.index)

    # --- Now drop buses not referenced by remaining components
    referenced_buses: set[str] = set()
    referenced_buses |= set(n.loads.bus.astype(str).unique())
    referenced_buses |= set(n.generators.bus.astype(str).unique())
    referenced_buses |= set(n.stores.bus.astype(str).unique())
    if not n.links.empty:
        for col in ("bus0", "bus1", "bus2", "bus3", "bus4"):
            if col in n.links.columns:
                referenced_buses |= set(n.links[col].dropna().astype(str).unique())

    keep_buses = n.buses.index[n.buses.index.isin(referenced_buses)]
    drop_buses = n.buses.index.difference(keep_buses)
    _safe_mremove(n, "Bus", drop_buses)

    # --- Drop carriers that are no longer used (optional; safe to leave as-is)
    # PyPSA does not require pruning carriers, but reducing noise helps inspection.
    used_carriers: set[str] = set()
    if hasattr(n, "buses") and not n.buses.empty:
        used_carriers |= set(n.buses.carrier.dropna().astype(str).unique())
    if hasattr(n, "links") and not n.links.empty and "carrier" in n.links:
        used_carriers |= set(n.links.carrier.dropna().astype(str).unique())
    if hasattr(n, "generators") and not n.generators.empty and "carrier" in n.generators:
        used_carriers |= set(n.generators.carrier.dropna().astype(str).unique())
    if hasattr(n, "stores") and not n.stores.empty and "carrier" in n.stores:
        used_carriers |= set(n.stores.carrier.dropna().astype(str).unique())

    drop_carriers = n.carriers.index.difference(pd.Index(sorted(used_carriers)))
    _safe_mremove(n, "Carrier", drop_carriers)

    return n


def add_exogenous_price_settlement(
    n: pypsa.Network,
    electricity_prices: pd.DataFrame,
    *,
    import_carrier: str = "grid import",
    export_carrier: str = "grid export",
    p_nom: float = 1e9,
) -> pypsa.Network:
    """
    Add a settlement layer that prices electricity at each AC bus with an exogenous
    marginal price time series.

    - Import: a large generator that can supply electricity at +price.
    - Export: a large *negative-sign* generator that can absorb electricity at -price,
      which acts like selling power to the grid at +price (revenue).

    Parameters
    ----------
    electricity_prices:
        DataFrame with index `n.snapshots` and columns equal to AC bus names.
        Values are prices in currency/MWh.
    """

    if electricity_prices.empty:
        raise ValueError("electricity_prices is empty")

    # Ensure snapshot alignment
    electricity_prices = electricity_prices.reindex(pd.DatetimeIndex(pd.to_datetime(n.snapshots)))
    if electricity_prices.isna().any().any():
        raise ValueError("electricity_prices contains NaNs after aligning to n.snapshots")

    ac_buses = n.buses.index[n.buses.carrier == "AC"]
    missing = sorted(set(map(str, ac_buses)) - set(map(str, electricity_prices.columns)))
    if missing:
        raise ValueError(
            "electricity_prices is missing AC buses. "
            f"Example missing: {missing[:10]}"
        )

    for carrier in (import_carrier, export_carrier):
        if carrier not in n.carriers.index:
            n.add("Carrier", carrier)

    import_names = []
    export_names = []

    for bus in ac_buses:
        import_name = f"grid_import_{bus}"
        export_name = f"grid_export_{bus}"
        import_names.append(import_name)
        export_names.append(export_name)

        if import_name not in n.generators.index:
            n.add(
                "Generator",
                import_name,
                bus=bus,
                carrier=import_carrier,
                p_nom=p_nom,
                p_nom_extendable=False,
                marginal_cost=0.0,  # set time-varying below
                sign=1.0,
            )

        if export_name not in n.generators.index:
            n.add(
                "Generator",
                export_name,
                bus=bus,
                carrier=export_carrier,
                p_nom=p_nom,
                p_nom_extendable=False,
                marginal_cost=0.0,  # set time-varying below
                # Negative sign means the generator consumes power at the bus;
                # with negative marginal cost, this yields revenue.
                sign=-1.0,
            )

    # PyPSA expects time-dependent marginal costs in generators_t.marginal_cost
    if not hasattr(n.generators_t, "marginal_cost"):
        n.generators_t.marginal_cost = pd.DataFrame(index=n.snapshots)

    for bus, import_name, export_name in zip(ac_buses, import_names, export_names, strict=True):
        price = electricity_prices[str(bus)].astype(float).reindex(n.snapshots)
        n.generators_t.marginal_cost[import_name] = price
        n.generators_t.marginal_cost[export_name] = -price

    return n

