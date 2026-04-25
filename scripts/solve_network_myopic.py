# SPDX-FileCopyrightText: : 2022 The PyPSA-China Authors, 2025 Ruike Lyu, rl8728@princeton.edu
#
# SPDX-License-Identifier: MIT

# coding: utf-8

import logging
import re
import copy
import numpy as np
import pandas as pd
import pypsa
import xarray as xr
import os
import time
from _helpers import (
    configure_logging,
    override_component_attrs,
)

# Add parallel computing related imports
import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor, as_completed
import functools

# Parameters allowed to be passed to PyPSA optimize
ALLOWED_OPTIMIZE_KWARGS = [
    "solver_name", "solver_options", "extra_functionality",
    "track_iterations", "min_iterations", "max_iterations"
]

logger = logging.getLogger(__name__)
# PyPSA logging API changed in v1.0 (module-level `pypsa.pf` removed).
try:
    pypsa.pf.logger.setLevel(logging.WARNING)  # type: ignore[attr-defined]
except Exception:
    # Best-effort: quiet common PyPSA loggers
    logging.getLogger("pypsa").setLevel(logging.WARNING)
    logging.getLogger("pypsa.pf").setLevel(logging.WARNING)
    logging.getLogger("pypsa.power_flow").setLevel(logging.WARNING)

# Set the log level of gurobipy and linopy to avoid outputting info information
import logging
gurobipy_logger = logging.getLogger('gurobipy')
gurobipy_logger.setLevel(logging.WARNING)

linopy_logger = logging.getLogger('linopy')
linopy_logger.setLevel(logging.WARNING)

# Set up linopy.modellog level
linopy_model_logger = logging.getLogger('linopy.model')
linopy_model_logger.setLevel(logging.WARNING)

from pypsa.descriptors import get_switchable_as_dense as get_as_dense

def prepare_network(
        n,
        solve_opts=None,
        using_single_node=False,
        single_node_province="Shandong"
):
    # Check if single node mode is enabled
    if using_single_node:
        # Filter to keep only specified province components
        province_buses = n.buses[n.buses.index.str.contains(single_node_province)].index
        
        # Remove non-province buses and their components
        non_province_buses = n.buses[~n.buses.index.isin(province_buses)].index
        
        # Remove generators not in specified province
        non_province_generators = n.generators[~n.generators.bus.isin(province_buses)].index
        n.mremove("Generator", non_province_generators)
        
        # Remove loads not in specified province
        non_province_loads = n.loads[~n.loads.bus.isin(province_buses)].index
        n.mremove("Load", non_province_loads)
        
        # Remove storage units not in specified province
        non_province_storage = n.storage_units[~n.storage_units.bus.isin(province_buses)].index
        n.mremove("StorageUnit", non_province_storage)
        
        # Remove stores not in specified province
        non_province_stores = n.stores[~n.stores.bus.isin(province_buses)].index
        n.mremove("Store", non_province_stores)
        
        # Remove links not connected to specified province
        non_province_links = n.links[~(n.links.bus0.isin(province_buses) | n.links.bus1.isin(province_buses))].index
        n.mremove("Link", non_province_links)
        
        # Remove lines not connected to specified province
        non_province_lines = n.lines[~(n.lines.bus0.isin(province_buses) | n.lines.bus1.isin(province_buses))].index
        n.mremove("Line", non_province_lines)
        
        # Finally remove non-province buses
        n.mremove("Bus", non_province_buses)
    
    # Fix any remaining links that might have undefined buses
    for link in n.links.index:
        if not (n.links.at[link, "bus0"] in n.buses.index and n.links.at[link, "bus1"] in n.buses.index):
            n.mremove("Link", [link])

    if "clip_p_max_pu" in solve_opts:
        for df in (
            n.generators_t.p_max_pu,
            n.generators_t.p_min_pu,
            n.storage_units_t.inflow,
        ):
            df.where(df > solve_opts["clip_p_max_pu"], other=0.0, inplace=True)

    load_shedding = solve_opts.get("load_shedding")
    if load_shedding:
        # intersect between macroeconomic and surveybased willingness to pay
        # http://journal.frontiersin.org/article/10.3389/fenrg.2015.00055/full
        # TODO: retrieve color and nice name from config
        n.add("Carrier", "load", color="#dd2e23", nice_name="Load shedding")
        buses_i = n.buses.query("carrier == 'AC'").index
        if not np.isscalar(load_shedding):
            # TODO: do not scale via sign attribute (use Eur/MWh instead of Eur/kWh)
            load_shedding = 1e2  # Eur/kWh

        n.madd(
            "Generator",
            buses_i,
            " load",
            bus=buses_i,
            carrier="load",
            sign=1e-3,  # Adjust sign to measure p and p_nom in kW instead of MW
            marginal_cost=load_shedding,  # Eur/kWh
            p_nom=1e9,  # kW
        )

    if solve_opts.get("noisy_costs"):
        for t in n.iterate_components():
            # if 'capital_cost' in t.df:
            #    t.df['capital_cost'] += 1e1 + 2.*(np.random.random(len(t.df)) - 0.5)
            if "marginal_cost" in t.df:
                t.df["marginal_cost"] += 1e-2 + 2e-3 * (
                    np.random.random(len(t.df)) - 0.5
                )

        for t in n.iterate_components(["Line", "Link"]):
            t.df["capital_cost"] += (
                1e-1 + 2e-2 * (np.random.random(len(t.df)) - 0.5)
            ) * t.df["length"]

    if solve_opts.get('nhours'):
        nhours = solve_opts['nhours']
        n.set_snapshots(n.snapshots[:nhours])
        n.snapshot_weightings[:] = 8760. / nhours

    return n

def add_chp_constraints(n):
    electric = (
        n.links.index.str.contains("CHP")
        & n.links.index.str.contains("generator")
    )
    heat = (
        n.links.index.str.contains("CHP")
        & n.links.index.str.contains("boiler")
    )

    electric_ext = n.links[electric].query("p_nom_extendable").index
    heat_ext = n.links[heat].query("p_nom_extendable").index

    electric_fix = n.links[electric].query("~p_nom_extendable").index
    heat_fix = n.links[heat].query("~p_nom_extendable").index

    p = n.model["Link-p"]  # dimension: [time, link]

    # output ratio between heat and electricity and top_iso_fuel_line for extendable
    if not electric_ext.empty:
        p_nom = n.model["Link-p_nom"]
        
        # Scale factors to improve numerical stability
        scale_factor = 1e-3  # Scale down by 1000
        
        # Get efficiency ratios with scaling
        elec_eff = (n.links.p_nom_ratio * n.links.efficiency)[electric_ext].values * scale_factor
        heat_eff = n.links.efficiency[heat_ext].values * scale_factor
        
        # Add constraint with scaled values
        lhs = (
            p_nom.loc[electric_ext] * elec_eff
            - p_nom.loc[heat_ext] * heat_eff
        )
        n.model.add_constraints(lhs == 0, name="chplink-fix_p_nom_ratio")

        # Scale the top_iso_fuel_line constraint
        # PyPSA/linopy dimension names changed across versions.
        # Older stacks used a `Link-ext` dimension for extendable assets; PyPSA>=1.0
        # uses `Link` directly. Rename only if the old dimension exists.
        p_nom_for_lhs = p_nom
        try:
            dims = getattr(getattr(p_nom_for_lhs, "data", None), "dims", ())
            if "Link-ext" in dims:
                p_nom_for_lhs = p_nom_for_lhs.rename({"Link-ext": "Link"})
        except Exception:
            p_nom_for_lhs = p_nom
        lhs = (
            p.loc[:, electric_ext] * scale_factor
            + p.loc[:, heat_ext] * scale_factor
            - p_nom_for_lhs.loc[electric_ext] * scale_factor
        )
        n.model.add_constraints(lhs <= 0, name="chplink-top_iso_fuel_line_ext")

    # top_iso_fuel_line for fixed
    if not electric_fix.empty:
        # Scale the fixed capacity constraint
        scale_factor = 1e-3
        lhs = p.loc[:, electric_fix] * scale_factor + p.loc[:, heat_fix] * scale_factor
        rhs = n.links.p_nom[electric_fix] * scale_factor
        n.model.add_constraints(lhs <= rhs, name="chplink-top_iso_fuel_line_fix")

    # back-pressure
    if not n.links[electric].index.empty:
        # Scale the back-pressure constraint
        scale_factor = 1e-3
        lhs = (
            p.loc[:, heat] * (n.links.efficiency[heat] * n.links.c_b[electric].values) * scale_factor
            - p.loc[:, electric] * n.links.efficiency[electric] * scale_factor
        )
        n.model.add_constraints(lhs <= 0, name="chplink-backpressure")

def add_transimission_constraints(n):
    """
    Add constraints for transmission lines that allow for asymmetric capacities
    while maintaining reasonable limits on the difference between directions.
    """
    if not n.links.p_nom_extendable.any():
        return

    positive_bool = n.links.index.str.contains("positive")
    negative_bool = n.links.index.str.contains("reversed")

    positive_ext = n.links[positive_bool].query("p_nom_extendable").index
    negative_ext = n.links[negative_bool].query("p_nom_extendable").index

    # Allow asymmetric capacities but limit the difference
    for pos in positive_ext:
        neg = pos.replace("positive", "reversed")
        if neg not in negative_ext:
            continue
            
        # Get the current capacities
        pos_cap = n.links.at[pos, "p_nom"]
        neg_cap = n.links.at[neg, "p_nom"]
        
        # Allow up to 20% difference between directions
        max_diff = 0.2 * max(pos_cap, neg_cap)
        
        # Add constraint: |pos_cap - neg_cap| <= max_diff
        lhs = n.model["Link-p_nom"].loc[pos] - n.model["Link-p_nom"].loc[neg]
        n.model.add_constraints(lhs <= max_diff, name=f"Link-transmission-{pos}-max")
        n.model.add_constraints(lhs >= -max_diff, name=f"Link-transmission-{pos}-min")

def add_retrofit_constraints(n):
    p_nom_max = pd.read_csv("data/p_nom/p_nom_max_cc.csv",index_col=0)
    planning_horizon = snakemake.wildcards.planning_horizons
    for year in range(int(planning_horizon) - 40, 2021, 5):
        coal = n.generators[(n.generators.carrier=="coal power plant") & (n.generators.build_year==year)].query("p_nom_extendable").index
        Bus = n.generators[(n.generators.carrier == "coal power plant") & (n.generators.build_year == year)].query(
            "p_nom_extendable").bus.values
        coal_retrofit = n.generators[n.generators.index.str.contains("retrofit")& (n.generators.build_year==year) & n.generators.bus.isin(Bus)].query("p_nom_extendable").index
        coal_retrofitted = n.generators[n.generators.index.str.contains("retrofit") & (n.generators.build_year==year) & n.generators.bus.isin(Bus)].query("~p_nom_extendable").groupby("bus").sum().p_nom_opt

        # Create a Series with proper index for the available capacity
        available_capacity = pd.Series(
            (p_nom_max[str(year)].loc[Bus] - coal_retrofitted.reindex(p_nom_max[str(year)].loc[Bus].index,fill_value=0)).values,
            index=Bus
        )

        # Create constraint with proper dimension handling
        for bus in Bus:
            if bus in coal_retrofitted.index:
                retrofit_cap = coal_retrofitted[bus]
            else:
                retrofit_cap = 0
                
            max_cap = p_nom_max[str(year)].loc[bus]
            coal_bus = coal[n.generators.loc[coal].bus == bus]
            coal_retrofit_bus = coal_retrofit[n.generators.loc[coal_retrofit].bus == bus]
            
            if len(coal_bus) > 0 or len(coal_retrofit_bus) > 0:
                lhs = n.model["Generator-p_nom"].loc[coal_bus].sum() + n.model["Generator-p_nom"].loc[coal_retrofit_bus].sum()
                rhs = max_cap - retrofit_cap
                n.model.add_constraints(lhs == rhs, name=f"Generator-coal-retrofit-{year}-{bus}")

def extra_functionality(n, snapshots, fixed_aluminum_usage=None):
    """
    Collects supplementary constraints which will be passed to ``pypsa.linopf.network_lopf``.
    If you want to enforce additional custom constraints, this is a good location to add them.
    The arguments ``opts`` and ``snakemake.config`` are expected to be attached to the network.
    """
    opts = n.opts
    config = n.config
    add_chp_constraints(n)
    add_transimission_constraints(n)
    if snakemake.wildcards.planning_horizons != "2020":
        add_retrofit_constraints(n)

def solve_aluminum_optimization(n, config, solving, opts="", nodal_prices=None, target_province=None, national_smelter_production=None, **kwargs):
    """
    Solution to the optimal operation problem of electrolytic aluminum - single province version
    Based on the node electricity price, the optimal operation problem of electrolytic aluminum is constrained by the operation to meet the aluminum demand.
    Solve only the optimization problem of electrolytic aluminum in specified provinces
    
    Parameters:
    -----------
    target_province : str
        Target province name, e.g., "Shandong", "Henan", etc.
    """
    set_of_options = solving["solver"]["options"]
    solver_options = solving["solver_options"][set_of_options] if set_of_options else {}
    solver_name = solving["solver"]["name"]
    
    # Use specialized solver settings for MILP problems
    if "MILP" in solving["solver_options"]:
        milp_solver_options = solving["solver_options"]["MILP"]
    else:
        milp_solver_options = solver_options
    
    # Read aluminum smelter annual production data and filter
    # from snakemake.inputGet the al_smelter_p_max file path in
    if 'snakemake' in globals():
        al_smelter_p_nom_path = snakemake.input.al_smelter_p_max
    else:
        # If snakemake is not available, use the default path
        al_smelter_p_nom_path = "data/p_nom/al_smelter_p_max.csv"
    
    al_smelter_annual_production = pd.read_csv(al_smelter_p_nom_path)
    al_smelter_annual_production = al_smelter_annual_production.set_index('Province')['p_nom']
    
    # Filter out annual output greater than 0.01 10kt/yearprovinces (consistent with prepare_base_network)
    al_smelter_annual_production = al_smelter_annual_production[al_smelter_annual_production > 0.01]
    
    # Convert annual production (10kt/year) to power capacity (MW)
    # 1 ton of aluminum requires ~13.3 MWh of electricity
    # Convert 10kt/year to MW: (10kt/year * 10000 * 13.3 MWh/ton) / (8760 hours/year) = MW
    al_smelter_p_nom = al_smelter_annual_production * 10000 * 13.3 / 8760  # Convert to MW
    
    # If no target province is specified, None is returned.
    if target_province is None:
        return None
    
    # Check if the target province is in the aluminum smelter annual production list
    if target_province not in al_smelter_p_nom.index:
        return None
    
    # Find electrolytic aluminum related components in the specified province
    aluminum_buses = n.buses[n.buses.carrier == "aluminum"].index
    aluminum_smelters = n.links[n.links.carrier == "aluminum"].index
    aluminum_stores = n.stores[n.stores.carrier == "aluminum"].index
    aluminum_loads = n.loads[n.loads.bus.isin(aluminum_buses)].index
    
    # Filter out electrolytic aluminum components in specified provinces
    target_aluminum_buses = [bus for bus in aluminum_buses if target_province in bus]
    # Filter out the electrolysers in the specified province
    target_aluminum_smelters = [smelter for smelter in aluminum_smelters if target_province in smelter]
    target_aluminum_stores = [store for store in aluminum_stores if target_province in store]
    target_aluminum_loads = [load for load in aluminum_loads if target_province in load]
    
    # If no electrolytic aluminum components are found in this province, None is returned.
    if not target_aluminum_smelters:
        return None
    
    # Obtain operating parameters of electrolytic aluminum plant
    from scripts.scenario_utils import get_aluminum_smelter_operational_params
    
    # Taking 250,000 tons/year as the benchmark production line, a single production line represents the entire province and is scaled accordingly.
    base_smelter_name = target_aluminum_smelters[0]
    base_bus0 = n.links.at[base_smelter_name, 'bus0']
    base_bus1 = n.links.at[base_smelter_name, 'bus1']
    base_efficiency = n.links.at[base_smelter_name, 'efficiency']
    original_smelter_name = base_smelter_name
    
    capacity_ratio = config.get('aluminum_capacity_ratio', 1.0)
    annual_production_10kt = al_smelter_annual_production[target_province] * capacity_ratio
    line_unit_10kt = 25.0
    if annual_production_10kt <= line_unit_10kt:
        scale_factor = 1.0
        line_cap_10kt = annual_production_10kt
    else:
        scale_factor = annual_production_10kt / line_unit_10kt
        line_cap_10kt = line_unit_10kt
    
    # Remove the original provincial electrolytic aluminum equipment
    n.mremove("Link", target_aluminum_smelters)
    
    # Add representative electrolytic aluminum production line
    target_aluminum_smelters = []
    line_cap_mw = line_cap_10kt * 10000 * 13.3 / 8760
    smelter_name = f"{target_province} aluminum smelter line-1"
    operational_params = get_aluminum_smelter_operational_params(
        config,
        al_smelter_p_nom=line_cap_mw
    )
    n.add(
        "Link",
        smelter_name,
        bus0=base_bus0,
        bus1=base_bus1,
        carrier="aluminum",
        p_nom=line_cap_mw,
        p_nom_extendable=False,
        efficiency=base_efficiency,
        capital_cost=operational_params['capital_cost'],
        stand_by_cost=operational_params['stand_by_cost'],
        marginal_cost=operational_params['marginal_cost'],
        start_up_cost=0.5 * operational_params['start_up_cost'],
        shut_down_cost=0.5 * operational_params['start_up_cost'],
        committable=True,
        p_min_pu=operational_params['p_min_pu']
    )
    target_aluminum_smelters.append(smelter_name)
    
    # Remove all non-electrolytic aluminum related components
    for component_type in ["Generator", "StorageUnit", "Store", "Link", "Load"]:
        if component_type == "Store":
            # Reserve electrolytic aluminum storage in designated provinces
            other_stores = n.stores[~n.stores.index.isin(target_aluminum_stores)].index
            n.mremove(component_type, other_stores)
        elif component_type == "Link":
            # Retain electrolytic aluminum smelting equipment in designated provinces
            other_links = n.links[~n.links.index.isin(target_aluminum_smelters)].index
            n.mremove(component_type, other_links)
        elif component_type == "Load":
            # Retain electrolytic aluminum load in specified provinces
            other_loads = n.loads[~n.loads.index.isin(target_aluminum_loads)].index
            n.mremove(component_type, other_loads)
        else:
            # Remove all other components
            n.mremove(component_type, n.df(component_type).index)
    
    # Remove nodes other than aluminum and AC
    non_aluminum_buses = n.buses[(n.buses.carrier != "aluminum") & (n.buses.carrier != "AC")].index
    n.mremove("Bus", non_aluminum_buses)
    
    # Make sure the virtual carrier exists
    if "virtual" not in n.carriers.index:
        n.add("Carrier", "virtual")
    
    # Add virtual generator to provide power (based on node power price)
    for bus in n.buses.index:
        if bus.endswith(" aluminum"):
            # This is an electrolytic aluminum node and does not require a virtual generator
            continue
            
        # Determine marginal cost based on node type
        if bus in nodal_prices.columns:
            # If the node has a corresponding marginal electricity price, use the electricity price of the node
            marginal_cost = nodal_prices[bus]
            
        n.add("Generator",
            f"virtual_gen_{bus}",
            bus=bus,
            carrier="virtual",
            p_nom=1e6,  # large capacity
            marginal_cost=marginal_cost)  # Using node marginal electricity price as marginal cost
    
    # Use the aluminum smelter output average of the province in the incoming national optimization results as the load
    target_aluminum_load = target_aluminum_loads[0] if target_aluminum_loads else None
    
    if target_aluminum_load and national_smelter_production and target_province in national_smelter_production:
        # Update load value to average output (scaled by representative production line)
        n.loads.at[target_aluminum_load, 'p_set'] = (
            national_smelter_production[target_province] / scale_factor
        )
    if target_aluminum_load and hasattr(n.loads_t, "p_set") and target_aluminum_load in n.loads_t.p_set.columns:
        n.loads_t.p_set[target_aluminum_load] = n.loads_t.p_set[target_aluminum_load] / scale_factor
    if target_aluminum_load and "p_set" in n.loads.columns and pd.notna(n.loads.at[target_aluminum_load, "p_set"]):
        n.loads.at[target_aluminum_load, "p_set"] = n.loads.at[target_aluminum_load, "p_set"] / scale_factor
    
    # Define the extra_functionality function dedicated to aluminum optimization
    def aluminum_extra_functionality(n_al, snapshots_al):
        # Find all aluminum smelters
        aluminum_smelters = n_al.links[n_al.links.carrier == "aluminum"].index
        
        if len(aluminum_smelters) > 0 and len(snapshots_al) > 0:
            first_snapshot = snapshots_al[0]
            last_snapshot = snapshots_al[-1]
            
            # Add first and last period power equality constraints for each aluminum smelter
            for smelter in aluminum_smelters:
                # Get the power variable for this smelter - note the indexing order: time first, then components
                first_smelter_p = n_al.model.variables["Link-p"].loc[first_snapshot, smelter]
                last_smelter_p = n_al.model.variables["Link-p"].loc[last_snapshot, smelter]
                
                # Add constraint: power in first period equals power in last period
                n_al.model.add_constraints(
                    first_smelter_p == last_smelter_p, 
                    name=f"aluminum-first-last-equal-{smelter}"
                )    
    def build_flat_aluminum_usage():
        snapshots = n.snapshots
        hours = len(snapshots)
        flat_al_tph = 0.0

        if annual_production_10kt > 0:
            # Annual output (10kt/year) -> tons/hour
            flat_al_tph = annual_production_10kt * 10000 / 8760
        elif (
            target_aluminum_load
            and hasattr(n.loads_t, "p_set")
            and target_aluminum_load in n.loads_t.p_set.columns
            and hours > 0
        ):
            total_tons = n.loads_t.p_set[target_aluminum_load].sum()
            flat_al_tph = total_tons / hours
        elif (
            target_aluminum_load
            and "p_set" in n.loads.columns
            and pd.notna(n.loads.at[target_aluminum_load, "p_set"])
        ):
            flat_al_tph = float(n.loads.at[target_aluminum_load, "p_set"])
        elif national_smelter_production and target_province in national_smelter_production:
            flat_al_tph = float(national_smelter_production[target_province])

        # tons/hour -> MW
        flat_power = flat_al_tph * 13.3
        per_line = flat_power / max(len(target_aluminum_smelters), 1)
        aluminum_usage_lines = pd.DataFrame(
            per_line,
            index=snapshots,
            columns=target_aluminum_smelters,
        )
        aluminum_usage = aluminum_usage_lines.sum(axis=1).to_frame(name=original_smelter_name)
        return aluminum_usage * scale_factor, aluminum_usage_lines * scale_factor

    # Solve electrolytic aluminum optimization problem
    # Only keep parameters supported by PyPSA
    optimize_kwargs = {k: v for k, v in kwargs.items() if k in ALLOWED_OPTIMIZE_KWARGS}
    try:
        status, condition = n.optimize(
            solver_name=solver_name,
            extra_functionality=aluminum_extra_functionality,
            **milp_solver_options,  # Use MILP-specific parameters
            **optimize_kwargs,
        )
    except Exception as exc:
        logger.warning(
            "Aluminum optimization exception for %s: %s",
            target_province,
            exc,
        )
        return build_flat_aluminum_usage()

    feasible_solution = (
        hasattr(n.links_t, "p0")
        and all(smelter in n.links_t.p0.columns for smelter in target_aluminum_smelters)
        and not n.links_t.p0[target_aluminum_smelters].isna().all().all()
    )

    no_feasible_conditions = {"infeasible", "no_solution", "infeasible_or_unbounded"}
    time_limit_conditions = {"time_limit", "time_limit_reached"}

    if (
        condition in no_feasible_conditions
        or (condition in time_limit_conditions and not feasible_solution)
        or not feasible_solution
    ):
        logger.warning(
            "Aluminum optimization fallback for %s: status=%s, condition=%s",
            target_province,
            status,
            condition,
        )
        return build_flat_aluminum_usage()

    # Energy consumption model for extracting electrolytic aluminum
    aluminum_usage_lines = n.links_t.p0[target_aluminum_smelters].copy()
    aluminum_usage = aluminum_usage_lines.sum(axis=1).to_frame(name=original_smelter_name)
    return aluminum_usage * scale_factor, aluminum_usage_lines * scale_factor

def solve_aluminum_optimization_parallel_wrapper(args):
    """
    Parallelizing packaging functions for electrolytic aluminum optimization
    This function needs to run in a separate process and therefore needs to receive all necessary parameters
    
    Parameters:
    -----------
    args : tuple
        tuple containing all necessary parameters:
        (original_network_path, original_overrides, config, solving, opts, 
         nodal_prices, target_province, solve_opts, using_single_node, 
         single_node_province, overrides_path, national_smelter_production, **kwargs)
    
    Returns:
    --------
    tuple : (province, aluminum_usage) or (province, None)
    """
    # Unpack parameters
    (original_network_path, original_overrides, config, solving, opts, 
     nodal_prices, target_province, solve_opts, using_single_node, 
     single_node_province, overrides_path, national_smelter_production, kwargs_dict) = args
    
    # Recreate the network
    if original_overrides:
        n_province = pypsa.Network(original_network_path, override_component_attrs=original_overrides)
    else:
        n_province = pypsa.Network(original_network_path)
    
    # Set network file path
    n_province._network_path = original_network_path
    if original_overrides:
        n_province._overrides_path = overrides_path
    
    # Reapply network preparation
    n_province = prepare_network(
        n_province,
        solve_opts,
        using_single_node=using_single_node,
        single_node_province=single_node_province
    )
    
    # Set configuration
    n_province.config = config
    n_province.opts = opts
    
    # Solve the optimization problem of electrolytic aluminum in a single province
    province_aluminum_usage, province_aluminum_usage_lines = solve_aluminum_optimization(
        n_province, 
        config, 
        solving, 
        opts, 
        nodal_prices=nodal_prices, 
        target_province=target_province,
        national_smelter_production=national_smelter_production,
        **kwargs_dict
    )
    
    return (target_province, province_aluminum_usage, province_aluminum_usage_lines)

def solve_aluminum_optimization_parallel(target_provinces, original_network_path, original_overrides, 
                                       config, solving, opts, current_nodal_prices, 
                                       solve_opts, using_single_node, single_node_province, 
                                       overrides_path, national_smelter_production=None, max_workers=None, **kwargs):
    """
    Solve electrolytic aluminum optimization problems in multiple provinces in parallel
    
    Parameters:
    -----------
    target_provinces : list
        List of provinces that need optimization
    original_network_path : str
        Original network file path
    original_overrides : dict or None
        Original overlay configuration
    config : dict
        Configuration dictionary
    solving : dict
        Solve configuration
    opts : list
        Options list
    current_nodal_prices : pd.DataFrame
        Current node electricity price
    solve_opts : dict
        Solving options
    using_single_node : bool
        Whether to use single node mode
    single_node_province : str
        Single node province name
    overrides_path : str
        Override file path
    max_workers : int, optional
        Maximum number of parallel processes, defaults to the number of CPU cores
    **kwargs : dict
        Other parameters
    
    Returns:
    --------
    tuple : (all_aluminum_usage, all_aluminum_usage_lines)
    """
    if not target_provinces:
        return {}, {}
    
    # Set the maximum number of parallel processes
    if max_workers is None:
        max_workers = min(len(target_provinces), mp.cpu_count())
    
    logger.info(
        "Aluminum parallel start: provinces=%s, max_workers=%s, mode=thread",
        len(target_provinces),
        max_workers,
    )
    
    # Prepare parameters
    args_list = []
    for province in target_provinces:
        args = (original_network_path, original_overrides, config, solving, opts,
                current_nodal_prices, province, solve_opts, using_single_node,
                single_node_province, overrides_path, national_smelter_production, kwargs)
        args_list.append(args)
    
    # Use thread pools for parallel execution to avoid multi-process serialization issues
    all_aluminum_usage = {}
    all_aluminum_usage_lines = {}
    
    start_time = time.time()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_province = {
            executor.submit(solve_aluminum_optimization_parallel_wrapper, args): args[6]  # args[6] is target_province
            for args in args_list
        }
        
        # Collect results
        for future in as_completed(future_to_province):
            result_province, province_aluminum_usage, province_aluminum_usage_lines = future.result()
            
            if province_aluminum_usage is not None:
                all_aluminum_usage[result_province] = province_aluminum_usage
            if province_aluminum_usage_lines is not None:
                all_aluminum_usage_lines[result_province] = province_aluminum_usage_lines
    
    total_time = time.time() - start_time
    logger.info(
        "Aluminum parallel done: provinces=%s, max_workers=%s, elapsed=%.2fs",
        len(target_provinces),
        max_workers,
        total_time,
    )
    
    return all_aluminum_usage, all_aluminum_usage_lines

def solve_network_iterative(n, config, solving, opts="", max_iterations=10, convergence_tolerance=0.01, **kwargs):
    """
    Electrolytic aluminum iterative optimization algorithm
    1. Use the continuous electrolytic aluminum model to solve and obtain the node electricity price and objective function value.
    2. Check convergence: compare the changes in the objective function value in step 1
    3. Based on the node electricity price, we ran the optimal operation problem of electrolytic aluminum and obtained a new energy consumption model of electrolytic aluminum.
    4. Fix the energy consumption of electrolytic aluminum and solve the remaining optimization problems
    5. Repeat 1-4 until convergence
    
    Convergence condition: The relative value of the change in the objective function in step 1 is less than convergence_tolerance (default 1%）
    """
    import time
    
    set_of_options = solving["solver"]["options"]
    solver_options = solving["solver_options"][set_of_options] if set_of_options else {}
    solver_name = solving["solver"]["name"]
    cf_solving = solving["options"]
    track_iterations = cf_solving.get("track_iterations", False)
    min_iterations = cf_solving.get("min_iterations", 4)
    max_transmission_iterations = cf_solving.get("max_iterations", 6)

    skip_iterations = cf_solving.get("skip_iterations", False)
    if not n.lines.s_nom_extendable.any():
        skip_iterations = True
    

    
    # Read the annual output data of aluminum smelters and obtain a list of provinces that need optimization
    if 'snakemake' in globals():
        al_smelter_p_nom_path = snakemake.input.al_smelter_p_max
    else:
        al_smelter_p_nom_path = "data/p_nom/al_smelter_p_max.csv"
    
    al_smelter_annual_production = pd.read_csv(al_smelter_p_nom_path)
    al_smelter_annual_production = al_smelter_annual_production.set_index('Province')['p_nom']
    al_smelter_annual_production = al_smelter_annual_production[al_smelter_annual_production > 0.01]
    
    # Convert annual production (10kt/year) to power capacity (MW)
    al_smelter_p_nom = al_smelter_annual_production * 10000 * 13.3 / 8760  # Convert to MW
    
    # Check which provinces actually have electrolytic aluminum components in the network
    available_provinces = []
    for province in al_smelter_p_nom.index:
        # Check whether there is electrolytic aluminum smelting equipment in the province
        aluminum_smelters = n.links[n.links.carrier == "aluminum"].index
        # Filter out the electrolysers in this province
        province_smelters = [smelter for smelter in aluminum_smelters if province in smelter]
        
        if province_smelters:
            available_provinces.append(province)
    
    target_provinces = available_provinces
    
    if not target_provinces:
        return None
    
    # Record total start time
    total_start_time = time.time()
    
    # Initialize electrolytic aluminum energy consumption model and objective function value
    aluminum_usage = None
    fixed_aluminum_usage = None
    fixed_line_usage = None
    fixed_national_smelter_production = None
    aluminum_removed_from_upper = False
    aluminum_snapshot = None
    previous_objective = None
    iteration = 0
    final_network = None
    
    # Record the time of each iteration
    iteration_times = []
    
    # Record solver performance statistics
    solver_performance = {
        'iteration_times': [],
        'objective_values': [],
        'convergence_info': []
    }
    
    # Save original network file paths and overrides for reloading
    original_network_path = None
    original_overrides = None
    if hasattr(n, '_network_path'):
        original_network_path = n._network_path
    if hasattr(n, '_overrides_path'):
        original_overrides = override_component_attrs(n._overrides_path)
    
    # Get single node parameters
    using_single_node = kwargs.get("using_single_node", False)
    single_node_province = kwargs.get("single_node_province", "Shandong")
    

    
    # Initialize the current network - only created on the first iteration
    n_current = None
    
    while iteration < max_iterations:
        iteration += 1
        iteration_start_time = time.time()  # Record the start time of each iteration
        

        
        # Network initialization strategy: use the last result as the initial value
        if iteration == 1:
            # First iteration: reloading the network
            if original_network_path:
                if original_overrides:
                    n_current = pypsa.Network(original_network_path, override_component_attrs=original_overrides)
                else:
                    n_current = pypsa.Network(original_network_path)
                
                # Set the network file path for possible subsequent reloads
                n_current._network_path = original_network_path
                if original_overrides:
                    n_current._overrides_path = kwargs.get("overrides_path", "data/override_component_attrs")
            
            # Reapply network preparation
            n_current = prepare_network(
                n_current,
                kwargs.get("solve_opts", {}),
                using_single_node=using_single_node,
                single_node_province=single_node_province
            )
            
            # Set configuration
            n_current.config = config
            n_current.opts = opts
        else:
            # Subsequent iterations: use the last network result as the initial value
            # n_currentAlready the result of the previous iteration, use it directly
            
            # Clear the last solution result but retain the network structure
            if hasattr(n_current, 'model'):
                del n_current.model
            if hasattr(n_current, 'objective'):
                del n_current.objective
        
        # Re-create the electrolytic aluminum smelting equipment, making sure to disable the electrolytic aluminum start-stop constraints in step 1
        aluminum_smelters = n_current.links[n_current.links.carrier == "aluminum"].index
        
        # Save original parameters
        smelter_params = {}
        for smelter in aluminum_smelters:
            smelter_params[smelter] = {
                'bus0': n_current.links.at[smelter, 'bus0'],
                'bus1': n_current.links.at[smelter, 'bus1'],
                'carrier': n_current.links.at[smelter, 'carrier'],
                'p_nom': n_current.links.at[smelter, 'p_nom'],
                'p_nom_extendable': n_current.links.at[smelter, 'p_nom_extendable'],
                'efficiency': n_current.links.at[smelter, 'efficiency'],
                'marginal_cost': n_current.links.at[smelter, 'marginal_cost'],
                'capital_cost': n_current.links.at[smelter, 'capital_cost'],
                'stand_by_cost': n_current.links.at[smelter, 'stand_by_cost'],
                'shut_down_cost': n_current.links.at[smelter, 'shut_down_cost'],
                'start_up_cost': n_current.links.at[smelter, 'start_up_cost'],
                'committable': n_current.links.at[smelter, 'committable'],
                'p_min_pu': n_current.links.at[smelter, 'p_min_pu']
            }
        
        # Remove existing smeller
        n_current.mremove("Link", aluminum_smelters)
        
        # Re-add the smelter and disable the electrolytic aluminum start-stop constraint in step 1
        for smelter_name, params in smelter_params.items():
            n_current.add("Link",
                smelter_name,
                bus0=params['bus0'],
                bus1=params['bus1'],
                carrier=params['carrier'],
                p_nom=params['p_nom'],
                p_nom_extendable=params['p_nom_extendable'],
                efficiency=params['efficiency'],
                marginal_cost=params['marginal_cost'],
                capital_cost=params['capital_cost'],
                start_up_cost=params['start_up_cost'],
                shut_down_cost=params['shut_down_cost'],
                stand_by_cost=params['stand_by_cost'],
                committable=False,  # Disable electrolytic aluminum start-stop constraints in step 1
                p_min_pu=0  # Set the minimum output to 0 in step 1
            )
        
        # Temporarily modify the configuration to disable electrolytic aluminum start-stop constraints
        original_commitment = config.get("aluminum_commitment", False)
        config["aluminum_commitment"] = False
        
        # If there is a fixed energy consumption of electrolytic aluminum, only the electrical load will be retained and aluminum-related components will be removed from the second iteration.
        if aluminum_usage is not None and iteration >= 2 and not aluminum_removed_from_upper:
            aluminum_usage_for_upper = fixed_aluminum_usage if fixed_aluminum_usage is not None else aluminum_usage
            
            if not hasattr(n_current.loads_t, 'p_set'):
                n_current.loads_t.p_set = get_as_dense(n_current, "Load", "p_set").copy()
            else:
                n_current.loads_t.p_set = n_current.loads_t.p_set.copy()
            
            for smelter in aluminum_usage_for_upper.columns:
                if " aluminum smelter" not in smelter:
                    continue
                
                province = smelter.split(" aluminum smelter")[0]
                load_name = province
                if load_name in n_current.loads_t.p_set.columns:
                    n_current.loads_t.p_set[load_name] = (
                        n_current.loads_t.p_set[load_name] + aluminum_usage_for_upper[smelter]
                    )
            
            aluminum_links = n_current.links[n_current.links.carrier == "aluminum"].index
            aluminum_transfer_links = n_current.links[n_current.links.carrier == "aluminum transfer"].index
            aluminum_stores = n_current.stores[n_current.stores.carrier == "aluminum"].index
            aluminum_buses = n_current.buses[n_current.buses.carrier == "aluminum"].index
            aluminum_transfer_buses = n_current.buses[n_current.buses.carrier == "aluminum transfer"].index
            aluminum_loads = n_current.loads[n_current.loads.bus.isin(aluminum_buses)].index
            
            n_current.mremove("Link", aluminum_links)
            n_current.mremove("Link", aluminum_transfer_links)
            n_current.mremove("Store", aluminum_stores)
            n_current.mremove("Load", aluminum_loads)
            n_current.mremove("Bus", aluminum_buses)
            n_current.mremove("Bus", aluminum_transfer_buses)
            
            aluminum_removed_from_upper = True
        
        # Solve the network
        # Only keep parameters supported by PyPSA
        optimize_kwargs = {k: v for k, v in kwargs.items() if k in ALLOWED_OPTIMIZE_KWARGS}
        
        current_solver_options = solver_options
        
        if skip_iterations:
            status, condition = n_current.optimize(
                solver_name=solver_name,
                solver_options=current_solver_options,  # Pass solver specific options using the solver_options parameter
                extra_functionality=extra_functionality,
                **optimize_kwargs,
            )
        else:
            status, condition = n_current.optimize.optimize_transmission_expansion_iteratively(
                solver_name=solver_name,
                solver_options=current_solver_options,  # Pass solver specific options using the solver_options parameter
                track_iterations=track_iterations,
                min_iterations=min_iterations,
                max_iterations=max_transmission_iterations,
                extra_functionality=extra_functionality,
                **optimize_kwargs,
            )
        
        # Recording solver performance statistics
        solver_performance['iteration_times'].append(time.time() - iteration_start_time)
        
        # Get the current objective function value
        current_objective = None
        if hasattr(n_current, 'objective'):
            current_objective = n_current.objective
        elif hasattr(n_current, 'model') and hasattr(n_current.model, 'objective_value'):
            current_objective = n_current.model.objective_value
        
        # Extract the node electricity price of the current iteration (for electrolytic aluminum optimization)
        current_nodal_prices = None
        if hasattr(n_current, 'buses_t') and hasattr(n_current.buses_t, 'marginal_price'):
            # Get all carriers as"AC"The marginal electricity price of the power node
            electricity_buses = n_current.buses[n_current.buses.carrier == "AC"].index
            if len(electricity_buses) > 0:
                # Use the marginal electricity price of all AC nodes
                current_nodal_prices = n_current.buses_t.marginal_price[electricity_buses]

        # Document first iteration of aluminum components and results for final drawing backfill
        if iteration == 1 and aluminum_snapshot is None:
            aluminum_buses = n_current.buses[n_current.buses.carrier.isin(["aluminum", "aluminum transfer"])].copy()
            aluminum_links = n_current.links[n_current.links.carrier.isin(["aluminum", "aluminum transfer"])].copy()
            aluminum_stores = n_current.stores[n_current.stores.carrier == "aluminum"].copy()
            aluminum_loads = n_current.loads[n_current.loads.bus.isin(aluminum_buses.index)].copy()
            aluminum_carriers = set(aluminum_buses.carrier.unique()) | set(aluminum_links.carrier.unique())
            
            links_t_p0 = None
            if hasattr(n_current.links_t, "p0") and not aluminum_links.empty:
                links_t_p0 = n_current.links_t.p0[aluminum_links.index].copy()
            
            stores_t_e = None
            if hasattr(n_current.stores_t, "e") and not aluminum_stores.empty:
                stores_t_e = n_current.stores_t.e[aluminum_stores.index].copy()
            
            loads_t_p_set = None
            if hasattr(n_current.loads_t, "p_set") and not aluminum_loads.empty:
                loads_t_p_set = n_current.loads_t.p_set[aluminum_loads.index].copy()
            
            aluminum_snapshot = {
                "buses": aluminum_buses,
                "links": aluminum_links,
                "stores": aluminum_stores,
                "loads": aluminum_loads,
                "carriers": aluminum_carriers,
                "links_t_p0": links_t_p0,
                "stores_t_e": stores_t_e,
                "loads_t_p_set": loads_t_p_set,
            }
        
        # Step 2: Check Convergence - Relative Value of Changes in Objective Function Based on Step 1
        if previous_objective is not None and current_objective is not None:
            # Calculate the relative value of the objective function change
            objective_change = abs(current_objective - previous_objective)
            relative_change = objective_change / abs(previous_objective) if abs(previous_objective) > 1e-10 else float('inf')
            
            if relative_change < convergence_tolerance:
                final_network = n_current
                break
        else:
            # In the first iteration, the objective function value is saved for next comparison.
            if current_objective is not None:
                previous_objective = current_objective
        
        # Restoring electrolytic aluminum start-stop constraints
        config["aluminum_commitment"] = True
        
        # Read the annual output data of aluminum smelters and obtain a list of provinces that need optimization
        if 'snakemake' in globals():
            al_smelter_p_nom_path = snakemake.input.al_smelter_p_max
        else:
            al_smelter_p_nom_path = "data/p_nom/al_smelter_p_max.csv"
        
        al_smelter_annual_production = pd.read_csv(al_smelter_p_nom_path)
        al_smelter_annual_production = al_smelter_annual_production.set_index('Province')['p_nom']
        al_smelter_annual_production = al_smelter_annual_production[al_smelter_annual_production > 0.01]
        
        # Convert annual production (10kt/year) to power capacity (MW)
        al_smelter_p_nom = al_smelter_annual_production * 10000 * 13.3 / 8760  # Convert to MW
        
        # Check which provinces actually have electrolytic aluminum components in the network
        if fixed_national_smelter_production is not None:
            target_provinces = list(fixed_national_smelter_production.keys())
        else:
            available_provinces = []
            for province in al_smelter_p_nom.index:
                # Check whether there is electrolytic aluminum smelting equipment in the province
                aluminum_smelters = n_current.links[n_current.links.carrier == "aluminum"].index
                # Filter out the electrolysers in this province
                province_smelters = [smelter for smelter in aluminum_smelters if province in smelter]
                
                if province_smelters:
                    available_provinces.append(province)
            
            target_provinces = available_provinces
        
        if not target_provinces:
            break
        
        # Obtain the average output of aluminum smelters in each province in the national optimization results
        if fixed_national_smelter_production is not None:
            national_smelter_production = fixed_national_smelter_production
        else:
            national_smelter_production = {}
            # Get production time series for all aluminum smelters
            all_aluminum_smelters = n_current.links[n_current.links.carrier == "aluminum"].index
            
            for province in target_provinces:
                # Find the electrolyser in this province
                province_smelters = [smelter for smelter in all_aluminum_smelters if province in smelter]
                if province_smelters:
                    # Get the production time series of all electrolyzers in the province
                    province_smelter_production = n_current.links_t.p1[province_smelters]
                    # Calculate average yield (take absolute value since yield is usually negative)
                    average_production = province_smelter_production.abs().mean().sum()
                    national_smelter_production[province] = average_production if average_production >= 1 else 0
        
        # Solve electrolytic aluminum optimization problems in multiple provinces (parallel by default)
        max_workers = kwargs.get("max_workers", 1)
        overrides_path = kwargs.get("overrides_path", "data/override_component_attrs")
        aluminum_optimize_kwargs = {
            k: v for k, v in kwargs.items() if k in ALLOWED_OPTIMIZE_KWARGS
        }
        all_aluminum_usage, all_aluminum_usage_lines = solve_aluminum_optimization_parallel(
            target_provinces=target_provinces,
            original_network_path=original_network_path,
            original_overrides=original_overrides,
            config=config,
            solving=solving,
            opts=opts,
            current_nodal_prices=current_nodal_prices,
            solve_opts=kwargs.get("solve_opts", {}),
            using_single_node=using_single_node,
            single_node_province=single_node_province,
            overrides_path=overrides_path,
            max_workers=max_workers,
            national_smelter_production=national_smelter_production,
            **aluminum_optimize_kwargs
        )
        
        if not all_aluminum_usage:
            break
        
        # Combined electrolytic aluminum energy consumption results for all provinces (provincial aggregation)
        # Get all smelting equipment names
        all_smelters = []
        for province_usage in all_aluminum_usage.values():
            all_smelters.extend(province_usage.columns.tolist())
        
        # Create merged DataFrame
        merged_aluminum_usage = pd.DataFrame(index=current_nodal_prices.index, columns=all_smelters)
        merged_aluminum_usage = merged_aluminum_usage.fillna(0).infer_objects(copy=False)
        
        # Populate data for each province
        for province, province_usage in all_aluminum_usage.items():
            for smelter in province_usage.columns:
                if smelter in merged_aluminum_usage.columns:
                    merged_aluminum_usage[smelter] = province_usage[smelter]
        
        # Merge line-level energy usage results (for backfilling and plotting)
        all_line_smelters = []
        for province_usage in all_aluminum_usage_lines.values():
            all_line_smelters.extend(province_usage.columns.tolist())
        
        merged_aluminum_usage_lines = pd.DataFrame(index=current_nodal_prices.index, columns=all_line_smelters)
        merged_aluminum_usage_lines = merged_aluminum_usage_lines.fillna(0).infer_objects(copy=False)
        
        for province, province_usage in all_aluminum_usage_lines.items():
            for smelter in province_usage.columns:
                if smelter in merged_aluminum_usage_lines.columns:
                    merged_aluminum_usage_lines[smelter] = province_usage[smelter]
        
        # Update electrolytic aluminum energy consumption and objective function values
        aluminum_usage = merged_aluminum_usage
        line_aluminum_usage = merged_aluminum_usage_lines
        if fixed_aluminum_usage is None:
            fixed_aluminum_usage = aluminum_usage.copy()
        if fixed_line_usage is None:
            fixed_line_usage = line_aluminum_usage.copy()
        if fixed_national_smelter_production is None:
            fixed_national_smelter_production = national_smelter_production.copy()
        previous_objective = current_objective
        final_network = n_current
        
        # Record the time of this iteration
        iteration_time = time.time() - iteration_start_time
        iteration_times.append(iteration_time)
    
    # Restore original configuration
    config["aluminum_commitment"] = original_commitment
    

    
    # If the aluminum component was removed from the upper layer, backfill the aluminum component from the first iteration with the results for the drawing
    if final_network is not None and aluminum_removed_from_upper and aluminum_snapshot is not None:
        # Make sure the carrier exists
        for carrier in aluminum_snapshot["carriers"]:
            if carrier not in final_network.carriers.index:
                final_network.add("Carrier", carrier)
        
        # Add aluminum related bus
        bus_attrs = final_network.component_attrs["Bus"].index
        for name, row in aluminum_snapshot["buses"].iterrows():
            if name in final_network.buses.index:
                continue
            attrs = row.reindex(bus_attrs).dropna().to_dict()
            final_network.add("Bus", name, **attrs)
        
        # Add provincial aluminum related links
        link_attrs = final_network.component_attrs["Link"].index
        for name, row in aluminum_snapshot["links"].iterrows():
            if name in final_network.links.index:
                continue
            attrs = row.reindex(link_attrs).dropna().to_dict()
            final_network.add("Link", name, **attrs)

        # Add production line-level aluminum related links (for drawing)
        if fixed_line_usage is not None:
            # Build a province->Production line capacity mapping
            province_line_caps = {}
            capacity_ratio = config.get('aluminum_capacity_ratio', 1.0)
            for prov in al_smelter_annual_production.index:
                annual_10kt = al_smelter_annual_production[prov] * capacity_ratio
                if annual_10kt <= 25.0:
                    line_caps_10kt = [annual_10kt]
                else:
                    full_lines = int(annual_10kt // 25.0)
                    remainder = annual_10kt - full_lines * 25.0
                    line_caps_10kt = [25.0] * full_lines
                    if remainder > 0:
                        line_caps_10kt.append(remainder)
                province_line_caps[prov] = line_caps_10kt
            
            for line_name in fixed_line_usage.columns:
                if " aluminum smelter line-" not in line_name:
                    continue
                province = line_name.split(" aluminum smelter line-")[0]
                base_name = f"{province} aluminum smelter"
                if base_name not in aluminum_snapshot["links"].index:
                    continue
                
                if line_name in final_network.links.index:
                    continue
                
                base_row = aluminum_snapshot["links"].loc[base_name]
                attrs = base_row.reindex(link_attrs).dropna().to_dict()
                
                try:
                    line_idx = int(line_name.split("line-")[1])
                except (IndexError, ValueError):
                    line_idx = None
                
                if line_idx is not None and province in province_line_caps:
                    caps_10kt = province_line_caps[province]
                    if 1 <= line_idx <= len(caps_10kt):
                        line_cap_mw = caps_10kt[line_idx - 1] * 10000 * 13.3 / 8760
                        attrs["p_nom"] = line_cap_mw
                        attrs["p_nom_extendable"] = False
                
                final_network.add("Link", line_name, **attrs)
        
        # Add aluminum related store
        store_attrs = final_network.component_attrs["Store"].index
        for name, row in aluminum_snapshot["stores"].iterrows():
            if name in final_network.stores.index:
                continue
            attrs = row.reindex(store_attrs).dropna().to_dict()
            final_network.add("Store", name, **attrs)
        
        # Add aluminum related load (for completeness)
        load_attrs = final_network.component_attrs["Load"].index
        for name, row in aluminum_snapshot["loads"].iterrows():
            if name in final_network.loads.index:
                continue
            attrs = row.reindex(load_attrs).dropna().to_dict()
            final_network.add("Load", name, **attrs)
        
        # Backfill aluminum load time series
        if aluminum_snapshot.get("loads_t_p_set") is not None:
            if not hasattr(final_network.loads_t, "p_set"):
                final_network.loads_t.p_set = pd.DataFrame(index=final_network.snapshots)
            for load in aluminum_snapshot["loads_t_p_set"].columns:
                final_network.loads_t.p_set[load] = aluminum_snapshot["loads_t_p_set"][load].reindex(final_network.snapshots)
        
        # Backfill time series (for plotting)
        if not hasattr(final_network.links_t, "p0"):
            final_network.links_t.p0 = pd.DataFrame(index=final_network.snapshots)

        if fixed_line_usage is not None:
            for link in fixed_line_usage.columns:
                final_network.links_t.p0[link] = fixed_line_usage[link].reindex(final_network.snapshots)
        
        # Recalculate aluminum storage capacity (use production line energy consumption corresponding to output - hourly demand)
        if not hasattr(final_network.stores_t, "e"):
            final_network.stores_t.e = pd.DataFrame(index=final_network.snapshots)
        
        if fixed_line_usage is not None:
            for store_name in aluminum_snapshot["stores"].index:
                if not store_name.endswith(" aluminum storage"):
                    continue
                province = store_name.replace(" aluminum storage", "")
                demand_load = f"{province} aluminum"
                if not hasattr(final_network.loads_t, "p_set") or demand_load not in final_network.loads_t.p_set.columns:
                    continue
                
                demand = final_network.loads_t.p_set[demand_load].reindex(final_network.snapshots)
                production = pd.Series(0.0, index=final_network.snapshots)
                
                for link in fixed_line_usage.columns:
                    if link.startswith(f"{province} aluminum smelter line-") and link in final_network.links.index:
                        efficiency = final_network.links.at[link, "efficiency"]
                        production = production + fixed_line_usage[link].reindex(final_network.snapshots) * efficiency
                
                storage = (production - demand).cumsum()
                storage = storage - storage.min()
                final_network.stores_t.e[store_name] = storage
        elif aluminum_snapshot["stores_t_e"] is not None:
            for store in aluminum_snapshot["stores_t_e"].columns:
                final_network.stores_t.e[store] = aluminum_snapshot["stores_t_e"][store].reindex(final_network.snapshots)
    
    # Return the final network results
    return final_network

def solve_network_standard(n, config, solving, opts="", **kwargs):
    """
    Standard network solution method (non-iterative)
    """
    set_of_options = solving["solver"]["options"]
    solver_options = solving["solver_options"][set_of_options] if set_of_options else {}
    solver_name = solving["solver"]["name"]
    cf_solving = solving["options"]
    track_iterations = cf_solving.get("track_iterations", False)
    min_iterations = cf_solving.get("min_iterations", 4)
    max_iterations = cf_solving.get("max_iterations", 6)

    # add to network for extra_functionality
    n.config = config
    n.opts = opts

    skip_iterations = cf_solving.get("skip_iterations", False)
    if not n.lines.s_nom_extendable.any():
        skip_iterations = True
    
    # Solve using standard parameters
    # Only keep parameters supported by PyPSA
    optimize_kwargs = {k: v for k, v in kwargs.items() if k in ALLOWED_OPTIMIZE_KWARGS}
    if skip_iterations:
        status, condition = n.optimize(
            solver_name=solver_name,
            solver_options=solver_options,  # Pass solver specific options using the solver_options parameter
            extra_functionality=extra_functionality,
            **optimize_kwargs,
        )
    else:
        status, condition = n.optimize.optimize_transmission_expansion_iteratively(
            solver_name=solver_name,
            solver_options=solver_options,  # Pass solver specific options using the solver_options parameter
            track_iterations=track_iterations,
            min_iterations=min_iterations,
            max_iterations=max_iterations,
            extra_functionality=extra_functionality,
            **optimize_kwargs,
        )

    # Store the objective value from the model.
    # In PyPSA v1.x, `Network.objective` is a read-only property; keep this as metadata.
    obj = None
    if hasattr(n.model, "objective_value"):
        obj = float(n.model.objective_value)
    elif hasattr(n.model, "objective"):
        obj = float(n.model.objective.value)
    if obj is not None:
        try:
            n.meta["objective"] = obj
        except Exception:
            # Best-effort: do not fail if meta is unavailable/unwritable
            pass

    return n

if __name__ == '__main__':
    if 'snakemake' not in globals():
        from _helpers import mock_snakemake
        snakemake = mock_snakemake('solve_network_myopic-1',
                                   opts='ll',
                                   topology='current+Neighbor',
                                   pathway='linear2050',
                                   co2_reduction='0.0',
                                   planning_horizons="2050")

    configure_logging(snakemake)

    opts = snakemake.wildcards.opts
    if "sector_opts" in snakemake.wildcards.keys():
        opts += "-" + snakemake.wildcards.sector_opts
    opts = [o for o in opts.split("-") if o != ""]
    solve_opts = snakemake.params.solving["options"]

    np.random.seed(solve_opts.get("seed", 123))

    if "overrides" in snakemake.input.keys():
        overrides = override_component_attrs(snakemake.input.overrides)
        n = pypsa.Network(snakemake.input.network, override_component_attrs=overrides)
    else:
        n = pypsa.Network(snakemake.input.network)
    
    # Set network file path for network reloading in iterative optimization
    n._network_path = snakemake.input.network
    if "overrides" in snakemake.input.keys():
        n._overrides_path = snakemake.input.overrides

    n = prepare_network(
        n,
        solve_opts,
        using_single_node=snakemake.params.using_single_node,
        single_node_province=snakemake.params.single_node_province
    )

    # Check whether electrolytic aluminum iterative optimization is enabled
    # Condition 1: Check snakemake.params.iterative_optimization
    # Condition 2: Check config["aluminum"]["grid_interaction"][planning_horizons]
    # Condition 3: Check config.get("add_aluminum", False)
    
    planning_horizons = snakemake.wildcards.planning_horizons
    
    # Check electrolytic aluminum excess rate conditions
    aluminum_grid_interaction_condition = False
    if (snakemake.config.get("aluminum", {}).get("grid_interaction", {}).get(planning_horizons, False)):
        aluminum_grid_interaction_condition = True
    
    # Check electrolytic aluminum function enabling conditions
    aluminum_enabled_condition = snakemake.config.get("add_aluminum", False)
    
    # Comprehensive judgment on whether to enable iterative optimization of electrolytic aluminum
    if (snakemake.params.iterative_optimization and 
        aluminum_grid_interaction_condition and 
        aluminum_enabled_condition):
        # Get iterative optimization parameters
        max_iterations = snakemake.config.get("aluminum_max_iterations", 10)
        convergence_tolerance = snakemake.config.get("aluminum_convergence_tolerance", 0.01)
        
        # Prepare the parameters passed to the iterator function
        iteration_kwargs = {
            "log_fn": snakemake.log.solver,
            "solve_opts": solve_opts,
            "using_single_node": snakemake.params.using_single_node,
            "single_node_province": snakemake.params.single_node_province
        }
        
        # If there are overrides, also pass them
        if "overrides" in snakemake.input.keys():
            iteration_kwargs["overrides"] = overrides
        
        # Add parallel computing configuration
        if snakemake.config.get("aluminum_parallel", True):  # Parallel computing is enabled by default
            max_workers = snakemake.config.get("aluminum_max_workers", 1)
            iteration_kwargs["max_workers"] = max_workers
        
        # Use iterative optimization algorithms
        n = solve_network_iterative(
            n,
            config=snakemake.config,
            solving=snakemake.params.solving,
            opts=opts,
            max_iterations=max_iterations,
            convergence_tolerance=convergence_tolerance,
            **iteration_kwargs,
        )
    else:
        # Use standard solving methods
        n = solve_network_standard(
            n,
            config=snakemake.config,
            solving=snakemake.params.solving,
            opts=opts,
            log_fn=snakemake.log.solver,
        )

    # Make sure the output directory exists
    output_dir = os.path.dirname(snakemake.output.network_name)
    os.makedirs(output_dir, exist_ok=True)

    #n.meta = dict(snakemake.config, **dict(wildcards=dict(snakemake.wildcards)))
    n.links_t.p2 = n.links_t.p2.astype(float)
    # Clean up links_t_p3 data before export to avoid dtype issues
    if hasattr(n, 'links_t') and hasattr(n.links_t, 'p3'):
        # Convert DataFrame to numeric, handling any non-numeric values
        n.links_t.p3 = n.links_t.p3.apply(pd.to_numeric, errors='coerce').fillna(0.0).infer_objects(copy=False)
    
    # Export results
    n.export_to_netcdf(snakemake.output.network_name) 