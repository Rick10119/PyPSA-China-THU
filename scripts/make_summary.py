# SPDX-FileCopyrightText: : 2022 The PyPSA-China Authors, 2025 Ruike Lyu, rl8728@princeton.edu
#
# SPDX-License-Identifier: MIT

"""
Create summary CSV files for all scenario runs including costs, capacities,
capacity factors, curtailment, energy balances, prices and other metrics.
"""

import logging

logger = logging.getLogger(__name__)

import sys
import os

import numpy as np
import pandas as pd
import pypsa
import matplotlib.pyplot as plt
from add_electricity import load_costs, update_transmission_costs
from scenario_utils import get_aluminum_smelter_operational_params

idx = pd.IndexSlice

opt_name = {"Store": "e", "Line": "s", "Transformer": "s"}

def assign_carriers(n):
    if "carrier" not in n.lines:
        n.lines["carrier"] = "AC"


def assign_locations(n):
    for c in n.iterate_components(n.one_port_components | n.branch_components):
        ifind = pd.Series(c.df.index.str.find(" ", start=4), c.df.index)
        for i in ifind.unique():
            names = ifind.index[ifind == i]
            if i == -1:
                c.df.loc[names, "location"] = ""
            else:
                c.df.loc[names, "location"] = names.str[:i]


def calculate_nodal_cfs(n, label, nodal_cfs):
    # Beware this also has extraneous locations for country (e.g. biomass) or continent-wide (e.g. fossil gas/oil) stuff
    for c in n.iterate_components(
        (n.branch_components ^ {"Line", "Transformer"})
        | n.controllable_one_port_components ^ {"Load", "StorageUnit"}
    ):
        capacities_c = c.df.groupby(["location", "carrier"])[
            opt_name.get(c.name, "p") + "_nom_opt"
        ].sum()

        if c.name == "Link":
            p = c.pnl.p0.abs().mean()
        elif c.name == "Generator":
            p = c.pnl.p.abs().mean()
        elif c.name == "Store":
            p = c.pnl.e.abs().mean()
        else:
            sys.exit()

        c.df["p"] = p
        p_c = c.df.groupby(["location", "carrier"])["p"].sum()

        cf_c = p_c / capacities_c

        index = pd.MultiIndex.from_tuples(
            [(c.list_name,) + t for t in cf_c.index.to_list()]
        )
        nodal_cfs = nodal_cfs.reindex(index.union(nodal_cfs.index))
        nodal_cfs.loc[index, label] = cf_c.values

    return nodal_cfs


def calculate_cfs(n, label, cfs):
    for c in n.iterate_components(
        n.branch_components
        | n.controllable_one_port_components ^ {"Load", "StorageUnit"}
    ):
        capacities_c = (
            c.df[opt_name.get(c.name, "p") + "_nom_opt"].groupby(c.df.carrier).sum()
        )

        if c.name in ["Link", "Line", "Transformer"]:
            p = c.pnl.p0.abs().mean()
        elif c.name == "Store":
            p = c.pnl.e.abs().mean()
        else:
            p = c.pnl.p.abs().mean()

        p_c = p.groupby(c.df.carrier).sum()

        cf_c = p_c / capacities_c

        cf_c = pd.concat([cf_c], keys=[c.list_name])

        cfs = cfs.reindex(cf_c.index.union(cfs.index))

        cfs.loc[cf_c.index, label] = cf_c

    return cfs


def calculate_nodal_costs(n, label, nodal_costs):
    # Beware this also has extraneous locations for country (e.g. biomass) or continent-wide (e.g. fossil gas/oil) stuff
    for c in n.iterate_components(
        n.branch_components | n.controllable_one_port_components ^ {"Load"}
    ):
        c.df["capital_costs"] = (
            c.df.capital_cost * c.df[opt_name.get(c.name, "p") + "_nom_opt"]
        )
        capital_costs = c.df.groupby(["location", "carrier"])["capital_costs"].sum()
        index = pd.MultiIndex.from_tuples(
            [(c.list_name, "capital") + t for t in capital_costs.index.to_list()]
        )
        nodal_costs = nodal_costs.reindex(index.union(nodal_costs.index))
        nodal_costs.loc[index, label] = capital_costs.values

        if c.name == "Link":
            p = c.pnl.p0.multiply(n.snapshot_weightings.generators, axis=0).sum()
        elif c.name == "Line":
            continue
        elif c.name == "StorageUnit":
            p_all = c.pnl.p.multiply(n.snapshot_weightings.generators, axis=0)
            p_all[p_all < 0.0] = 0.0
            p = p_all.sum()
        else:
            p = c.pnl.p.multiply(n.snapshot_weightings.generators, axis=0).sum()

        # correct sequestration cost
        if c.name == "Store":
            items = c.df.index[
                (c.df.carrier == "co2 stored") & (c.df.marginal_cost <= -100.0)
            ]
            c.df.loc[items, "marginal_cost"] = -20.0

        c.df["marginal_costs"] = p * c.df.marginal_cost
        marginal_costs = c.df.groupby(["location", "carrier"])["marginal_costs"].sum()
        index = pd.MultiIndex.from_tuples(
            [(c.list_name, "marginal") + t for t in marginal_costs.index.to_list()]
        )
        nodal_costs = nodal_costs.reindex(index.union(nodal_costs.index))
        nodal_costs.loc[index, label] = marginal_costs.values

        # Calculate startup costs for committable components (only for aluminum)
        if (hasattr(c, 'pnl') and 'start_up_cost' in c.df.columns and
            c.name == "Link" and "aluminum" in c.df.carrier.unique()):
            # Find aluminum smelters
            aluminum_smelters = c.df.index[c.df.carrier == "aluminum"]
            
            if len(aluminum_smelters) > 0:
                # For aluminum smelters, calculate status from power data (continuous model)
                p0_data = c.pnl.p0[aluminum_smelters]
                # Status is 1 when power > 1 (MW, threshold set by me), 0 when power = 0
                status = (p0_data > 1).astype(int)
                
                # Calculate startup events (status changes from 0 to 1) only for aluminum
                startup_events = (status.diff() > 0).sum()  # Count transitions from 0 to 1
                
                # Calculate startup costs per line using link-specific costs
                startup_costs = startup_events * c.df.loc[aluminum_smelters, "start_up_cost"]
                c.df.loc[aluminum_smelters, "startup_costs"] = startup_costs
                startup_costs = c.df.loc[aluminum_smelters].groupby(["location", "carrier"])["startup_costs"].sum()
                index = pd.MultiIndex.from_tuples(
                    [(c.list_name, "startup") + t for t in startup_costs.index.to_list()]
                )
                nodal_costs = nodal_costs.reindex(index.union(nodal_costs.index))
                nodal_costs.loc[index, label] = startup_costs.values

        # Calculate shutdown costs for committable components (only for aluminum)
        if (hasattr(c, 'pnl') and 'shut_down_cost' in c.df.columns and
            c.name == "Link" and "aluminum" in c.df.carrier.unique()):
            # Find aluminum smelters
            aluminum_smelters = c.df.index[c.df.carrier == "aluminum"]
            
            if len(aluminum_smelters) > 0:
                # For aluminum smelters, calculate status from power data (continuous model)
                p0_data = c.pnl.p0[aluminum_smelters]
                # Status is 1 when power > 1 (MW, threshold set by me), 0 when power = 0
                status = (p0_data > 1).astype(int)
                
                # Calculate shutdown events (status changes from 1 to 0) only for aluminum
                shutdown_events = (status.diff() < 0).sum()  # Count transitions from 1 to 0
                
                # Calculate shutdown costs per line using link-specific costs
                shutdown_costs = shutdown_events * c.df.loc[aluminum_smelters, "shut_down_cost"]
                c.df.loc[aluminum_smelters, "shutdown_costs"] = shutdown_costs
                shutdown_costs = c.df.loc[aluminum_smelters].groupby(["location", "carrier"])["shutdown_costs"].sum()
                index = pd.MultiIndex.from_tuples(
                    [(c.list_name, "shutdown") + t for t in shutdown_costs.index.to_list()]
                )
                nodal_costs = nodal_costs.reindex(index.union(nodal_costs.index))
                nodal_costs.loc[index, label] = shutdown_costs.values

        # Calculate standby costs for committable components (only for aluminum)
        if (hasattr(c, 'pnl') and 'stand_by_cost' in c.df.columns and
            c.name == "Link" and "aluminum" in c.df.carrier.unique()):
            # Find aluminum smelters
            aluminum_smelters = c.df.index[c.df.carrier == "aluminum"]
            
            if len(aluminum_smelters) > 0:
                # For aluminum smelters, calculate status from power data (continuous model)
                p0_data = c.pnl.p0[aluminum_smelters]
                # Status is 1 when power > 0, 0 when power = 0
                status = (p0_data > 0).astype(int)
                
                # Calculate total standby hours by multiplying status with time weights
                # Ensure both have the same index (time periods)
                standby_hours = (status.multiply(n.snapshot_weightings.generators, axis=0)).sum()
                
                # Calculate standby costs per line using link-specific costs
                standby_costs = standby_hours * c.df.loc[aluminum_smelters, "stand_by_cost"]
                c.df.loc[aluminum_smelters, "standby_costs"] = standby_costs
                standby_costs = c.df.loc[aluminum_smelters].groupby(["location", "carrier"])["standby_costs"].sum()
                index = pd.MultiIndex.from_tuples(
                    [(c.list_name, "standby") + t for t in standby_costs.index.to_list()]
                )
                nodal_costs = nodal_costs.reindex(index.union(nodal_costs.index))
                nodal_costs.loc[index, label] = standby_costs.values

        # Special handling for aluminum smelters in iterative optimization
        # When iterative_optimization is True, aluminum smelters use continuous model without status
        if (c.name == "Link" and "aluminum" in c.df.carrier.unique() and 
            hasattr(n, 'config') and n.config.get('iterative_optimization', False) and
            'start_up_cost' in c.df.columns):
            
            # Find aluminum smelters
            aluminum_smelters = c.df.index[c.df.carrier == "aluminum"]
            
            if len(aluminum_smelters) > 0:
                # Calculate startup events manually for aluminum smelters
                # Count transitions from 0 to > 0 power (only true startup events)
                p0_data = c.pnl.p0[aluminum_smelters]
                # Create a mask for when previous power was 0 and current power > 0
                startup_events = ((p0_data.shift(1) == 0) & (p0_data > 0)).sum()
                
                # Calculate startup costs using operational params
                operational_params = get_aluminum_smelter_operational_params(config, 
                                                                          config.get('aluminum', {}).get('smelter_flexibility'),
                                                                          c.df.loc[aluminum_smelters, 'p_nom_opt'].iloc[0] if len(aluminum_smelters) > 0 else None)
                startup_cost_value = operational_params.get('start_up_cost', 0.0)
                c.df.loc[aluminum_smelters, "startup_costs"] = startup_events * startup_cost_value
                startup_costs = c.df.loc[aluminum_smelters].groupby(["location", "carrier"])["startup_costs"].sum()
                index = pd.MultiIndex.from_tuples(
                    [(c.list_name, "startup") + t for t in startup_costs.index.to_list()]
                )
                nodal_costs = nodal_costs.reindex(index.union(nodal_costs.index))
                nodal_costs.loc[index, label] = startup_costs.values

    return nodal_costs


def calculate_costs(n, label, costs):
    for c in n.iterate_components(
        n.branch_components | n.controllable_one_port_components ^ {"Load"}
    ):
        # Calculate capital costs
        capital_costs = c.df.capital_cost * c.df[opt_name.get(c.name, "p") + "_nom_opt"]
        capital_costs_grouped = capital_costs.groupby(c.df.carrier).sum()
        
        # Add component type and cost type as index levels
        capital_costs_grouped = pd.concat([capital_costs_grouped], keys=["capital"])
        capital_costs_grouped = pd.concat([capital_costs_grouped], keys=[c.list_name])
        
        # Update costs DataFrame
        costs = costs.reindex(capital_costs_grouped.index.union(costs.index))
        costs.loc[capital_costs_grouped.index, label] = capital_costs_grouped

        # Calculate marginal costs based on component type
        if c.name == "Link":
            p = c.pnl.p0.multiply(n.snapshot_weightings.generators, axis=0).sum()
        elif c.name == "Line":
            continue
        elif c.name == "StorageUnit":
            p_all = c.pnl.p.multiply(n.snapshot_weightings.generators, axis=0)
            p_all[p_all < 0.0] = 0.0
            p = p_all.sum()
        else:
            p = c.pnl.p.multiply(n.snapshot_weightings.generators, axis=0).sum()

        # Special case for CO2 storage
        if c.name == "Store":
            items = c.df.index[
                (c.df.carrier == "co2 stored") & (c.df.marginal_cost <= -100.0)
            ]
            c.df.loc[items, "marginal_cost"] = -20.0

        # Calculate marginal costs
        marginal_costs = p * c.df.marginal_cost
        marginal_costs_grouped = marginal_costs.groupby(c.df.carrier).sum()
        
        # Add component type and cost type as index levels
        marginal_costs_grouped = pd.concat([marginal_costs_grouped], keys=["marginal"])
        marginal_costs_grouped = pd.concat([marginal_costs_grouped], keys=[c.list_name])
        
        # Update costs DataFrame
        costs = costs.reindex(marginal_costs_grouped.index.union(costs.index))
        costs.loc[marginal_costs_grouped.index, label] = marginal_costs_grouped

        # Calculate startup costs for committable components (only for aluminum)
        if (hasattr(c, 'pnl') and 'start_up_cost' in c.df.columns and
            c.name == "Link" and "aluminum" in c.df.carrier.unique()):
            # Find aluminum smelters
            aluminum_smelters = c.df.index[c.df.carrier == "aluminum"]
            
            if len(aluminum_smelters) > 0:
                # For aluminum smelters, calculate status from power data (continuous model)
                p0_data = c.pnl.p0[aluminum_smelters]
                # Status is 1 when power > 1 (MW, threshold set by me), 0 when power = 0
                status = (p0_data > 1).astype(int)
                
                # Calculate startup events (status changes from 0 to 1) only for aluminum
                startup_events = (status.diff() > 0).sum()  # Count transitions from 0 to 1
                
                # Calculate startup costs per line using link-specific costs
                startup_costs = startup_events * c.df.loc[aluminum_smelters, "start_up_cost"]
                startup_costs_grouped = startup_costs.groupby(c.df.loc[aluminum_smelters, 'carrier']).sum()
                
                # Add component type and cost type as index levels
                startup_costs_grouped = pd.concat([startup_costs_grouped], keys=["startup"])
                startup_costs_grouped = pd.concat([startup_costs_grouped], keys=[c.list_name])
                
                # Update costs DataFrame
                costs = costs.reindex(startup_costs_grouped.index.union(costs.index))
                costs.loc[startup_costs_grouped.index, label] = startup_costs_grouped

        # Calculate shutdown costs for committable components (only for aluminum)
        if (hasattr(c, 'pnl') and 'shut_down_cost' in c.df.columns and
            c.name == "Link" and "aluminum" in c.df.carrier.unique()):
            # Find aluminum smelters
            aluminum_smelters = c.df.index[c.df.carrier == "aluminum"]
            
            if len(aluminum_smelters) > 0:
                # For aluminum smelters, calculate status from power data (continuous model)
                p0_data = c.pnl.p0[aluminum_smelters]
                # Status is 1 when power > 0, 0 when power = 0
                status = (p0_data > 0).astype(int)
                
                # Calculate shutdown events (status changes from 1 to 0) only for aluminum
                shutdown_events = (status.diff() < 0).sum()  # Count transitions from 1 to 0
                
                # Calculate shutdown costs per line using link-specific costs
                shutdown_costs = shutdown_events * c.df.loc[aluminum_smelters, "shut_down_cost"]
                shutdown_costs_grouped = shutdown_costs.groupby(c.df.loc[aluminum_smelters, 'carrier']).sum()
                
                # Add component type and cost type as index levels
                shutdown_costs_grouped = pd.concat([shutdown_costs_grouped], keys=["shutdown"])
                shutdown_costs_grouped = pd.concat([shutdown_costs_grouped], keys=[c.list_name])
                
                # Update costs DataFrame
                costs = costs.reindex(shutdown_costs_grouped.index.union(costs.index))
                costs.loc[shutdown_costs_grouped.index, label] = shutdown_costs_grouped

        # Calculate standby costs for committable components (only for aluminum)
        if (hasattr(c, 'pnl') and 'stand_by_cost' in c.df.columns and
            c.name == "Link" and "aluminum" in c.df.carrier.unique()):
            # Find aluminum smelters
            aluminum_smelters = c.df.index[c.df.carrier == "aluminum"]
            
            if len(aluminum_smelters) > 0:
                # For aluminum smelters, calculate status from power data (continuous model)
                p0_data = c.pnl.p0[aluminum_smelters]
                # Status is 1 when power > 0, 0 when power = 0
                status = (p0_data > 0).astype(int)
                
                # Calculate total standby time (when status is 1 but not generating) only for aluminum
                # Calculate total standby hours by multiplying status with time weights
                # Ensure both have the same index (time periods)
                standby_hours = (status.multiply(n.snapshot_weightings.generators, axis=0)).sum()
                
                # Calculate standby costs per line using link-specific costs
                standby_costs = standby_hours * c.df.loc[aluminum_smelters, "stand_by_cost"]
                standby_costs_grouped = standby_costs.groupby(c.df.loc[aluminum_smelters, 'carrier']).sum()
                
                # Add component type and cost type as index levels
                standby_costs_grouped = pd.concat([standby_costs_grouped], keys=["standby"])
                standby_costs_grouped = pd.concat([standby_costs_grouped], keys=[c.list_name])
                
                # Update costs DataFrame
                costs = costs.reindex(standby_costs_grouped.index.union(costs.index))
                costs.loc[standby_costs_grouped.index, label] = standby_costs_grouped

        # Iterative optimization already handled by per-line startup costs above

    # add back in all hydro
    # costs.loc[("storage_units", "capital", "hydro"),label] = (0.01)*2e6*n.storage_units.loc[n.storage_units.group=="hydro", "p_nom"].sum()
    # costs.loc[("storage_units", "capital", "PHS"),label] = (0.01)*2e6*n.storage_units.loc[n.storage_units.group=="PHS", "p_nom"].sum()
    # costs.loc[("generators", "capital", "ror"),label] = (0.02)*3e6*n.generators.loc[n.generators.group=="ror", "p_nom"].sum()

    return costs


def calculate_nodal_capacities(n, label, nodal_capacities):
    # Beware this also has extraneous locations for country (e.g. biomass) or continent-wide (e.g. fossil gas/oil) stuff
    for c in n.iterate_components(
        n.branch_components | n.controllable_one_port_components ^ {"Load"}
    ):
        nodal_capacities_c = c.df.groupby(["location", "carrier"])[
            opt_name.get(c.name, "p") + "_nom_opt"
        ].sum()
        index = pd.MultiIndex.from_tuples(
            [(c.list_name,) + t for t in nodal_capacities_c.index.to_list()]
        )
        nodal_capacities = nodal_capacities.reindex(index.union(nodal_capacities.index))
        nodal_capacities.loc[index, label] = nodal_capacities_c.values

    return nodal_capacities


def calculate_capacities(n, label, capacities):
    for c in n.iterate_components(
        n.branch_components | n.controllable_one_port_components ^ {"Load"}
    ):
        capacities_grouped = (
            c.df[opt_name.get(c.name, "p") + "_nom_opt"].groupby(c.df.carrier).sum()
        )
        capacities_grouped = pd.concat([capacities_grouped], keys=[c.list_name])

        capacities = capacities.reindex(
            capacities_grouped.index.union(capacities.index)
        )

        capacities.loc[capacities_grouped.index, label] = capacities_grouped

    return capacities


def calculate_curtailment(n, label, curtailment):
    avail = (
        n.generators_t.p_max_pu.multiply(n.generators.p_nom_opt)
        .sum()
        .groupby(n.generators.carrier)
        .sum()
    )
    used = n.generators_t.p.sum().groupby(n.generators.carrier).sum()

    curtailment[label] = (((avail - used) / avail) * 100).round(3)

    return curtailment


def calculate_energy(n, label, energy):
    """
    Calculate the total energy for each component in the network.
    
    Parameters:
    -----------
    n : pypsa.Network
        The network object containing all components
    label : str
        The label/identifier for the current scenario
    energy : pd.DataFrame
        DataFrame to store the calculated energy values
        
    Returns:
    --------
    pd.DataFrame
        Updated energy DataFrame with new calculations
    """
    
    # Iterate through all components (both one-port and branch components)
    for c in n.iterate_components(n.one_port_components | n.branch_components):
        
        # Handle one-port components (like generators, loads, storage units)
        if c.name in n.one_port_components:
            logger.debug(f"Processing one-port component: {c.name}")
            
            # Check if pnl data exists and has the expected columns
            if not hasattr(c, 'pnl') or 'p' not in c.pnl:
                logger.warning(f"No pnl data found for {c.name}")
                continue
                
            # Filter out components that don't exist in pnl data
            valid_components = c.df.index.intersection(c.pnl.p.columns)
            if len(valid_components) == 0:
                logger.warning(f"No valid components found for {c.name} in pnl data")
                continue
                
            # Calculate energy by:
            # 1. Multiply power by snapshot weightings (to account for time periods)
            # 2. Sum over all time periods
            # 3. Multiply by sign (to handle consumption vs generation)
            # 4. Group by carrier type
            try:
                c_energies = (
                    c.pnl.p[valid_components].multiply(n.snapshot_weightings.generators, axis=0)
                    .sum()
                    .multiply(c.df.loc[valid_components, "sign"])
                    .groupby(c.df.loc[valid_components, "carrier"])
                    .sum()
                )
            except Exception as e:
                logger.warning(f"Error calculating energy for {c.name}: {str(e)}")
                continue
        else:
            logger.debug(f"Processing branch component: {c.name}")
            # For branch components (like lines, transformers, links)
            # Initialize empty series with zeros for each carrier
            c_energies = pd.Series(0.0, c.df.carrier.unique())
            
            # Process each port of the branch component
            # (e.g., bus0, bus1 for a line)
            for port in [col[3:] for col in c.df.columns if col[:3] == "bus"]:
                # Skip bus2 for hydro turbine links
                if c.name == "Link" and port == "2" and "hydroelectricity" in c.df.carrier.unique():
                    logger.debug("Skipping bus2 for hydro turbine links")
                    continue
                # Skip bus3 for aluminum smelters
                if c.name == "Link" and port == "3" and "aluminum smelter" in c.df.carrier.unique():
                    logger.debug("Skipping bus3 for aluminum smelters")
                    continue
                    
                logger.debug(f"Processing port: {port}")
                
                # Skip if power flow data is missing for this port
                if "p" + port not in c.pnl:
                    logger.warning(f"Skipping port {port} for {c.name} as power flow data is missing")
                    continue
                    
                try:
                    # Calculate total energy flow through each port
                    totals = (
                        c.pnl["p" + port]
                        .multiply(n.snapshot_weightings.generators, axis=0)
                        .sum()
                    )
                    
                    # Handle cases where bus is missing (bug in nomopyomo)
                    no_bus = c.df.index[c.df["bus" + port] == ""]
                    # Only process links that exist in both totals and no_bus
                    valid_no_bus = no_bus.intersection(totals.index)
                    if not valid_no_bus.empty:
                        totals.loc[valid_no_bus] = float(
                            n.component_attrs[c.name].loc["p" + port, "default"]
                        )                    
                    # Subtract the port's energy from total (to account for flow direction)
                    c_energies -= totals.groupby(c.df.carrier).sum()
                except Exception as e:
                    logger.warning(f"Error processing port {port} for {c.name}: {str(e)}")
                    continue  # Skip this port and continue with others instead of raising the error

        # Add component name as first level of index
        c_energies = pd.concat([c_energies], keys=[c.list_name])

        # Ensure the energy DataFrame has all necessary indices
        energy = energy.reindex(c_energies.index.union(energy.index))

        # Store the calculated energies in the DataFrame
        energy.loc[c_energies.index, label] = c_energies

    return energy


def calculate_supply(n, label, supply):
    """
    Calculate the maximum power dispatch (supply) of each component at the buses, aggregated by carrier.
    
    This function calculates the peak power flow through each component in the network,
    which represents the maximum capacity utilization of each technology.
    
    Parameters:
    -----------
    n : pypsa.Network
        The network object containing all components and their data
    label : str
        The label/identifier for the current scenario (e.g., year)
    supply : pd.DataFrame
        DataFrame to store the calculated supply values
        
    Returns:
    --------
    pd.DataFrame
        Updated supply DataFrame with maximum dispatch values for each component
    """
    # Get unique bus carriers (e.g., AC, DC, heat, etc.)
    bus_carriers = n.buses.carrier.unique()

    # Process each bus carrier type separately
    for i in bus_carriers:
        # Create a boolean map of buses belonging to this carrier
        bus_map = n.buses.carrier == i
        bus_map.at[""] = False  # Exclude empty bus names

        # Process one-port components (generators, loads, storage units)
        for c in n.iterate_components(n.one_port_components):
            # Find components connected to buses of this carrier
            items = c.df.index[c.df.bus.map(bus_map).fillna(False).infer_objects(copy=False)]

            if len(items) == 0:
                continue

            # Filter out components that don't exist in pnl data
            valid_items = items.intersection(c.pnl.p.columns)
            if len(valid_items) == 0:
                logger.warning(f"No valid items found for {c.name} in pnl data")
                continue

            # Calculate maximum power flow, accounting for component sign
            # (positive for generation, negative for consumption)
            s = (
                c.pnl.p[valid_items]
                .max()  # Get maximum power flow
                .multiply(c.df.loc[valid_items, "sign"])  # Apply sign convention
                .groupby(c.df.loc[valid_items, "carrier"])  # Group by technology type
                .sum()  # Sum over all components of same carrier
            )
            # Add component type and bus carrier as index levels
            s = pd.concat([s], keys=[c.list_name])
            s = pd.concat([s], keys=[i])

            # Update the supply DataFrame
            supply = supply.reindex(s.index.union(supply.index))
            supply.loc[s.index, label] = s

        # Process branch components (links, lines, transformers)
        for c in n.iterate_components(n.branch_components):
            # Process each port of the branch component
            for end in [col[3:] for col in c.df.columns if col[:3] == "bus"]:
                # Find components connected to buses of this carrier at this port
                items = c.df.index[c.df["bus" + end].map(bus_map).fillna(False)]

                if len(items) == 0:
                    continue

                # Skip if power flow data is missing for this port
                if "p" + end not in c.pnl:
                    logger.warning(f"Skipping port {end} for {c.name} as power flow data is missing")
                    continue

                # Filter out components without power flow data
                valid_items = items.intersection(c.pnl["p" + end].columns)
                if len(valid_items) == 0:
                    logger.warning(f"No valid items found for port {end} of {c.name}")
                    continue

                # Calculate maximum power flow with sign compensation
                s = (-1) ** (1 - int(end)) * (
                    (-1) ** int(end) * c.pnl["p" + end][valid_items]
                ).max().groupby(c.df.loc[valid_items, "carrier"]).sum()
                s.index = s.index + end
                s = pd.concat([s], keys=[c.list_name])
                s = pd.concat([s], keys=[i])

                supply = supply.reindex(s.index.union(supply.index))
                supply.loc[s.index, label] = s

    return supply


def calculate_supply_energy(n, label, supply_energy):
    """
    Calculate the total energy supply/consumption of each component at the buses,
    aggregated by carrier.
    
    This function calculates the total energy flow through each component over time,
    which represents the actual energy production/consumption.
    
    Parameters:
    -----------
    n : pypsa.Network
        The network object containing all components and their data
    label : str
        The label/identifier for the current scenario (e.g., year)
    supply_energy : pd.DataFrame
        DataFrame to store the calculated energy values
        
    Returns:
    --------
    pd.DataFrame
        Updated supply_energy DataFrame with total energy values for each component
    """
    # Get unique bus carriers
    bus_carriers = n.buses.carrier.unique()

    # Process each bus carrier type
    for i in bus_carriers:
        # Create boolean map of buses belonging to this carrier
        bus_map = n.buses.carrier == i
        bus_map.at[""] = False

        # Process one-port components
        for c in n.iterate_components(n.one_port_components):
            items = c.df.index[c.df.bus.map(bus_map).fillna(False)]

            if len(items) == 0:
                continue

            # Filter out components that don't exist in pnl data
            valid_items = items.intersection(c.pnl.p.columns)
            if len(valid_items) == 0:
                logger.warning(f"No valid items found for {c.name} in pnl data")
                continue

            # Calculate total energy flow over time
            # Multiply by snapshot weightings to account for time periods
            s = (
                c.pnl.p[valid_items]
                .multiply(n.snapshot_weightings.generators, axis=0)  # Weight by time period
                .sum()  # Sum over all time periods
                .multiply(c.df.loc[valid_items, "sign"])  # Apply sign convention
                .groupby(c.df.loc[valid_items, "carrier"])  # Group by technology
                .sum()  # Sum over all components of same carrier
            )
            # Add component type and bus carrier as index levels
            s = pd.concat([s], keys=[c.list_name])
            s = pd.concat([s], keys=[i])

            # Update the supply_energy DataFrame
            supply_energy = supply_energy.reindex(s.index.union(supply_energy.index))
            supply_energy.loc[s.index, label] = s

        # Process branch components
        for c in n.iterate_components(n.branch_components):
            for end in [col[3:] for col in c.df.columns if col[:3] == "bus"]:
                items = c.df.index[c.df["bus" + str(end)].map(bus_map).fillna(False)]

                if len(items) == 0:
                    continue

                # Skip if power flow data is missing
                if "p" + end not in c.pnl:
                    logger.warning(f"Skipping port {end} for {c.name} as power flow data is missing")
                    continue

                # Filter out components without power flow data
                valid_items = items.intersection(c.pnl["p" + end].columns)
                if len(valid_items) == 0:
                    logger.warning(f"No valid items found for port {end} of {c.name}")
                    continue

                # Calculate total energy flow with sign compensation
                s = (-1) * c.pnl["p" + end][valid_items].multiply(
                    n.snapshot_weightings.generators, axis=0  # Weight by time period
                ).sum().groupby(c.df.loc[valid_items, "carrier"]).sum()
                s.index = s.index + end  # Add port number to index
                s = pd.concat([s], keys=[c.list_name])
                s = pd.concat([s], keys=[i])

                # Update the supply_energy DataFrame
                supply_energy = supply_energy.reindex(
                    s.index.union(supply_energy.index)
                )
                supply_energy.loc[s.index, label] = s

    return supply_energy


def calculate_metrics(n, label, metrics):
    metrics_list = [
        "line_volume",
        "line_volume_limit",
        "line_volume_AC",
        "line_volume_DC",
        "line_volume_shadow",
        "co2_shadow",
    ]

    metrics = metrics.reindex(pd.Index(metrics_list).union(metrics.index))

    metrics.at["line_volume_DC", label] = (n.links.length * n.links.p_nom_opt)[
        n.links.carrier == "DC"
    ].sum()
    metrics.at["line_volume_AC", label] = (n.lines.length * n.lines.s_nom_opt).sum()
    metrics.at["line_volume", label] = metrics.loc[
        ["line_volume_AC", "line_volume_DC"], label
    ].sum()

    if "lv_limit" in n.global_constraints.index:
        metrics.at["line_volume_limit", label] = n.global_constraints.at[
            "lv_limit", "constant"
        ]
        metrics.at["line_volume_shadow", label] = n.global_constraints.at[
            "lv_limit", "mu"
        ]

    if "co2_limit" in n.global_constraints.index:
        metrics.at["co2_shadow", label] = n.global_constraints.at["co2_limit", "mu"]

    return metrics


def calculate_prices(n, label, prices):
    prices = prices.reindex(prices.index.union(n.buses.carrier.unique()))

    # WARNING: this is time-averaged, see weighted_prices for load-weighted average
    prices[label] = n.buses_t.marginal_price.mean().groupby(n.buses.carrier).mean()

    return prices


def calculate_weighted_prices(n, label, weighted_prices):
    # Warning: doesn't include storage units as loads

    weighted_prices = weighted_prices.reindex(
        pd.Index(
            [
                "electricity",
                "heat",
                "space heat",
                "urban heat",
                "space urban heat",
                "gas",
                "H2",
            ]
        )
    )

    link_loads = {
        "electricity": [
            "heat pump",
            "resistive heater",
            "battery charger",
            "H2 Electrolysis",
        ],
        "heat": ["water tanks charger"],
        "urban heat": ["water tanks charger"],
        "space heat": [],
        "space urban heat": [],
        "gas": ["OCGT", "gas boiler", "CHP electric", "CHP heat"],
        "H2": ["Sabatier", "H2 Fuel Cell"],
    }

    for carrier in link_loads:
        if carrier == "electricity":
            suffix = ""
        elif carrier[:5] == "space":
            suffix = carrier[5:]
        else:
            suffix = " " + carrier

        buses = n.buses.index[n.buses.index.str[2:] == suffix]

        if buses.empty:
            continue

        if carrier in ["H2", "gas"]:
            load = pd.DataFrame(index=n.snapshots, columns=buses, data=0.0)
        elif carrier[:5] == "space":
            load = heat_demand_df[buses.str[:2]].rename(
                columns=lambda i: str(i) + suffix
            )
        else:
            load = n.loads_t.p_set[buses]

        for tech in link_loads[carrier]:
            names = n.links.index[n.links.index.to_series().str[-len(tech) :] == tech]

            if names.empty:
                continue

            load += (
                n.links_t.p0[names].T.groupby(n.links.loc[names, "bus0"]).sum().T
            )

        # Add H2 Store when charging
        # if carrier == "H2":
        #    stores = n.stores_t.p[buses+ " Store"].groupby(n.stores.loc[buses+ " Store", "bus"],axis=1).sum(axis=1)
        #    stores[stores > 0.] = 0.
        #    load += -stores

        weighted_prices.loc[carrier, label] = (
            load * n.buses_t.marginal_price[buses]
        ).sum().sum() / load.sum().sum()

        # still have no idea what this is for, only for debug reasons.
        if carrier[:5] == "space":
            logger.debug(load * n.buses_t.marginal_price[buses])

    return weighted_prices


def calculate_market_values(n, label, market_values):
    # Warning: doesn't include storage units

    carrier = "AC"

    buses = n.buses.index[n.buses.carrier == carrier]
    
    # Only use buses that exist in marginal_price data
    available_buses = buses.intersection(n.buses_t.marginal_price.columns)
    
    if available_buses.empty:
        logger.warning("No available buses found for market value calculation")
        return market_values

    ## First do market value of generators ##

    generators = n.generators.index[n.buses.loc[n.generators.bus, "carrier"] == carrier]

    # Filter out generators that don't exist in pnl data
    valid_generators = generators.intersection(n.generators_t.p.columns)
    if len(valid_generators) == 0:
        logger.warning("No valid generators found for market value calculation")
    else:
        techs = n.generators.loc[valid_generators, "carrier"].value_counts().index

        market_values = market_values.reindex(market_values.index.union(techs))

        for tech in techs:
            gens = valid_generators[n.generators.loc[valid_generators, "carrier"] == tech]

            try:
                dispatch = (
                    n.generators_t.p[gens]
                    .T.groupby(n.generators.loc[gens, "bus"])
                    .sum()
                    .T
                    .reindex(columns=available_buses, fill_value=0.0)
                )

                revenue = dispatch * n.buses_t.marginal_price[available_buses]

                market_values.at[tech, label] = revenue.sum().sum() / dispatch.sum().sum()
            except Exception as e:
                logger.warning(f"Error calculating market value for generator tech {tech}: {str(e)}")
                continue

    ## Now do market value of links ##

    for i in ["0", "1"]:
        all_links = n.links.index[n.buses.loc[n.links["bus" + i], "carrier"] == carrier]

        # Filter out links that don't exist in pnl data
        valid_links = all_links.intersection(n.links_t["p" + i].columns)
        if len(valid_links) == 0:
            logger.warning(f"No valid links found for port {i} in market value calculation")
            continue

        techs = n.links.loc[valid_links, "carrier"].value_counts().index

        market_values = market_values.reindex(market_values.index.union(techs))

        for tech in techs:
            links = valid_links[n.links.loc[valid_links, "carrier"] == tech]

            try:
                dispatch = (
                    n.links_t["p" + i][links]
                    .T.groupby(n.links.loc[links, "bus" + i])
                    .sum()
                    .T
                    .reindex(columns=available_buses, fill_value=0.0)
                )

                revenue = dispatch * n.buses_t.marginal_price[available_buses]

                market_values.at[tech, label] = revenue.sum().sum() / dispatch.sum().sum()
            except Exception as e:
                logger.warning(f"Error calculating market value for link tech {tech} port {i}: {str(e)}")
                continue

    return market_values


def calculate_price_statistics(n, label, price_statistics):
    price_statistics = price_statistics.reindex(
        price_statistics.index.union(
            pd.Index(["zero_hours", "mean", "standard_deviation"])
        )
    )

    buses = n.buses.index[n.buses.carrier == "AC"]
    
    # Only use buses that exist in marginal_price data
    available_buses = buses.intersection(n.buses_t.marginal_price.columns)
    
    if available_buses.empty:
        # If no buses available, set default values
        price_statistics.at["zero_hours", label] = 0.0
        price_statistics.at["mean", label] = 0.0
        price_statistics.at["standard_deviation", label] = 0.0
        return price_statistics

    threshold = 0.1  # higher than phoney marginal_cost of wind/solar

    df = pd.DataFrame(data=0.0, columns=available_buses, index=n.snapshots)

    df[n.buses_t.marginal_price[available_buses] < threshold] = 1.0

    price_statistics.at["zero_hours", label] = df.sum().sum() / (
        df.shape[0] * df.shape[1]
    )

    price_statistics.at["mean", label] = (
        n.buses_t.marginal_price[available_buses].unstack().mean()
    )

    price_statistics.at["standard_deviation", label] = (
        n.buses_t.marginal_price[available_buses].unstack().std()
    )

    return price_statistics


def calculate_aluminum_statistics(n, label, aluminum_statistics):
    """
    Calculate aluminum-related statistics including electricity costs and other metrics.
    
    Parameters:
    -----------
    n : pypsa.Network
        The network object containing all components
    label : str
        The label/identifier for the current scenario
    aluminum_statistics : pd.DataFrame
        DataFrame to store the calculated aluminum statistics
        
    Returns:
    --------
    pd.DataFrame
        Updated aluminum_statistics DataFrame with new calculations
    """
    
    # Define the statistics we want to calculate
    stats_list = [
        "total_electricity_cost",  # The sum of the products of annual electricity consumption and node marginal electricity price
        "total_electricity_consumption",  # Total electricity consumption throughout the year
        "average_electricity_price",  # average electricity price
        "max_electricity_price",  # Maximum electricity price
        "min_electricity_price",  # lowest electricity price
        "electricity_cost_per_mwh",  # Average cost of electricity per MWh
        "aluminum_capacity",  # Aluminum smelting capacity
        "aluminum_storage_capacity",  # Aluminum storage capacity
        "aluminum_utilization_rate",  # Aluminum smelting utilization rate
        "total_startup_events",  # Total number of starts
        "total_shutdown_events",  # total number of closures
    ]
    
    aluminum_statistics = aluminum_statistics.reindex(
        aluminum_statistics.index.union(pd.Index(stats_list))
    )
    
    # Get electricity buses (AC carrier)
    electricity_buses = n.buses.index[n.buses.carrier == "AC"]
    
    # Only use buses that exist in marginal_price data
    available_buses = electricity_buses.intersection(n.buses_t.marginal_price.columns)
    
    if available_buses.empty:
        logger.warning("No available electricity buses found for aluminum statistics")
        # Set default values
        for stat in stats_list:
            aluminum_statistics.at[stat, label] = 0.0
        return aluminum_statistics
    
    # Find aluminum smelter links
    aluminum_smelters = n.links.index[n.links.carrier == "aluminum"]
    
    if len(aluminum_smelters) == 0:
        logger.warning("No aluminum smelters found in the network")
        # Set default values
        for stat in stats_list:
            aluminum_statistics.at[stat, label] = 0.0
        return aluminum_statistics
    
    # Calculate electricity consumption and costs for aluminum smelters
    total_electricity_cost = 0.0
    total_electricity_consumption = 0.0
    
    # Count the number of starts and stops
    total_startup_events = 0
    total_shutdown_events = 0
    
    # Statistics of start and stop times by province
    province_startup_events = {}
    province_shutdown_events = {}
    
    # Process each aluminum smelter
    for smelter in aluminum_smelters:
        # Get the bus where the smelter is connected (bus0 for electricity input)
        smelter_bus = n.links.loc[smelter, "bus0"]
        
        # Check if the bus exists in marginal price data
        if smelter_bus not in n.buses_t.marginal_price.columns:
            logger.warning(f"Smelter {smelter} bus {smelter_bus} not found in marginal price data")
            continue
        
        # Get electricity consumption (p0 is typically negative for consumption)
        if smelter in n.links_t.p0.columns:
            electricity_consumption = n.links_t.p0[smelter].abs()  # Use absolute value
            electricity_prices = n.buses_t.marginal_price[smelter_bus]
            
            # Calculate total electricity consumption (MWh)
            total_consumption = (
                electricity_consumption.multiply(n.snapshot_weightings.generators, axis=0)
                .sum()
            )
            
            # Calculate total electricity cost (EUR)
            total_cost = (
                electricity_consumption.multiply(n.snapshot_weightings.generators, axis=0)
                .multiply(electricity_prices, axis=0)
                .sum()
            )
            
            total_electricity_consumption += total_consumption
            total_electricity_cost += total_cost
            
            # Calculate the number of starts and stops
            # state：When the power > 0 in running state(1)，When the power = 0 in stop state(0)
            status = (electricity_consumption > 0).astype(int)
            
            # Start event：Status changes from 0 to 1 (from stop to run)
            startup_events = (status.diff() > 0).sum()
            # close event：Status changes from 1 to 0 (from running to stopping)
            shutdown_events = (status.diff() < 0).sum()
            
            total_startup_events += startup_events
            total_shutdown_events += shutdown_events
            
            # Statistics of start and stop times by province
            # Extract province information from smelter name
            province = None
            for prov in ["Anhui", "Shandong", "Henan", "Xinjiang", "InnerMongolia", "Gansu", "Qinghai", "Ningxia", "Yunnan", "Guizhou", "Sichuan", "Chongqing", "Hubei", "Hunan", "Jiangxi", "Fujian", "Guangdong", "Guangxi", "Hainan", "Tibet"]:
                if prov in smelter:
                    province = prov
                    break
            
            if province is None:
                # If the province cannot be identified from its name，Try to identify from bus name
                for prov in ["Anhui", "Shandong", "Henan", "Xinjiang", "InnerMongolia", "Gansu", "Qinghai", "Ningxia", "Yunnan", "Guizhou", "Sichuan", "Chongqing", "Hubei", "Hunan", "Jiangxi", "Fujian", "Guangdong", "Guangxi", "Hainan", "Tibet"]:
                    if prov in smelter_bus:
                        province = prov
                        break
            
            if province is None:
                province = "Unknown"
            
            # Accumulate the number of starts and stops in this province
            if province not in province_startup_events:
                province_startup_events[province] = 0
                province_shutdown_events[province] = 0
            
            province_startup_events[province] += startup_events
            province_shutdown_events[province] += shutdown_events
    
    # Print the statistics of start and stop times in each province
    logger.debug(f"=== Statistics on start and stop times of electrolytic aluminum ({label}) ===")
    logger.debug(f"Total number of starts: {total_startup_events}")
    logger.debug(f"total number of closures: {total_shutdown_events}")
    logger.debug("Details of the number of starts and stops in each province:")
    
    for province in sorted(province_startup_events.keys()):
        startup_count = province_startup_events[province]
        shutdown_count = province_shutdown_events[province]
        logger.debug(f"  {province}: start up {startup_count} Second-rate, closure {shutdown_count} Second-rate")
    
    # Calculate aluminum capacity (from links)
    aluminum_capacity = n.links.loc[aluminum_smelters, "p_nom_opt"].sum()
    
    # Calculate aluminum storage capacity (from stores)
    aluminum_stores = n.stores.index[n.stores.carrier == "aluminum"]
    aluminum_storage_capacity = n.stores.loc[aluminum_stores, "e_nom_opt"].sum() if len(aluminum_stores) > 0 else 0.0
    
    # Calculate utilization rate (actual consumption vs capacity)
    if aluminum_capacity > 0:
        # Convert capacity from MW to MWh for the year (8760 hours)
        annual_capacity = aluminum_capacity * 8760
        aluminum_utilization_rate = total_electricity_consumption / annual_capacity if annual_capacity > 0 else 0.0
    else:
        aluminum_utilization_rate = 0.0
    
    # Calculate average electricity cost per MWh
    electricity_cost_per_mwh = (
        total_electricity_cost / total_electricity_consumption 
        if total_electricity_consumption > 0 else 0.0
    )
    
    # Calculate price statistics for all electricity buses
    all_prices = n.buses_t.marginal_price[available_buses].unstack()
    average_electricity_price = all_prices.mean() if len(all_prices) > 0 else 0.0
    max_electricity_price = all_prices.max() if len(all_prices) > 0 else 0.0
    min_electricity_price = all_prices.min() if len(all_prices) > 0 else 0.0
    
    # Store all statistics
    aluminum_statistics.at["total_electricity_cost", label] = total_electricity_cost
    aluminum_statistics.at["total_electricity_consumption", label] = total_electricity_consumption
    aluminum_statistics.at["average_electricity_price", label] = average_electricity_price
    aluminum_statistics.at["max_electricity_price", label] = max_electricity_price
    aluminum_statistics.at["min_electricity_price", label] = min_electricity_price
    aluminum_statistics.at["electricity_cost_per_mwh", label] = electricity_cost_per_mwh
    aluminum_statistics.at["aluminum_capacity", label] = aluminum_capacity
    aluminum_statistics.at["aluminum_storage_capacity", label] = aluminum_storage_capacity
    aluminum_statistics.at["aluminum_utilization_rate", label] = aluminum_utilization_rate
    aluminum_statistics.at["total_startup_events", label] = total_startup_events
    aluminum_statistics.at["total_shutdown_events", label] = total_shutdown_events
    
    return aluminum_statistics


def calculate_emissions(n, label, emissions):
    """
    Calculate monthly CO2 emissions from coal and natural gas
    
    Parameters:
    -----------
    n : pypsa.Network
        The network object containing all components
    label : str
        The label/identifier for the current scenario
    emissions : pd.DataFrame
        DataFrame to store the calculated emissions data
        
    Returns:
    --------
    pd.DataFrame
        Updated emissions DataFrame with coal and gas emissions by month
    """
    
    # Define emission metrics
    emissions_list = [
        "total_coal_emissions",  # total coal emissions
        "total_gas_emissions",   # Total natural gas emissions
        # Monthly coal emissions
        "coal_emissions_jan", "coal_emissions_feb", "coal_emissions_mar", "coal_emissions_apr",
        "coal_emissions_may", "coal_emissions_jun", "coal_emissions_jul", "coal_emissions_aug",
        "coal_emissions_sep", "coal_emissions_oct", "coal_emissions_nov", "coal_emissions_dec",
        # Monthly natural gas emissions
        "gas_emissions_jan", "gas_emissions_feb", "gas_emissions_mar", "gas_emissions_apr",
        "gas_emissions_may", "gas_emissions_jun", "gas_emissions_jul", "gas_emissions_aug",
        "gas_emissions_sep", "gas_emissions_oct", "gas_emissions_nov", "gas_emissions_dec",
    ]
    
    emissions = emissions.reindex(
        emissions.index.union(pd.Index(emissions_list))
    )
    
    # Initialize monthly emissions dictionary
    monthly_coal_emissions = {}
    monthly_gas_emissions = {}
    
    # Get emission factors
    carrier_emissions = n.carriers.co2_emissions if hasattr(n.carriers, 'co2_emissions') else pd.Series(dtype=float)
    
    # Handle the generator
    if hasattr(n, 'generators_t') and hasattr(n.generators_t, 'p'):
        gen_dispatch = n.generators_t.p
        
        for generator in gen_dispatch.columns:
            if generator in n.generators.index:
                carrier = n.generators.at[generator, 'carrier']
                emission_factor = carrier_emissions.get(carrier, 0.0)
                
                # Only deals with coal and natural gas related technologies
                if 'coal' in carrier.lower() or 'gas' in carrier.lower():
                    # Calculate monthly energy production
                    monthly_data = gen_dispatch[generator].multiply(n.snapshot_weightings.generators, axis=0)
                    monthly_energy = monthly_data.resample('M').sum()
                    monthly_gen_emissions = monthly_energy * emission_factor
                    
                    # Assigned to coal or natural gas based on carrier type
                    if 'coal' in carrier.lower():
                        for month_idx, month_emissions in monthly_gen_emissions.items():
                            month_key = f"coal_emissions_{month_idx.strftime('%b').lower()}"
                            monthly_coal_emissions[month_key] = monthly_coal_emissions.get(month_key, 0.0) + month_emissions
                    elif 'gas' in carrier.lower():
                        for month_idx, month_emissions in monthly_gen_emissions.items():
                            month_key = f"gas_emissions_{month_idx.strftime('%b').lower()}"
                            monthly_gas_emissions[month_key] = monthly_gas_emissions.get(month_key, 0.0) + month_emissions
    
    # Handle links（CHP、converter etc.）
    if hasattr(n, 'links_t') and hasattr(n.links_t, 'p0'):
        link_power = n.links_t.p0
        
        for link in link_power.columns:
            if link in n.links.index:
                carrier = n.links.at[link, 'carrier']
                emission_factor = carrier_emissions.get(carrier, 0.0)
                
                # Only deals with coal and natural gas related technologies
                if 'coal' in carrier.lower() or 'gas' in carrier.lower():
                    # Calculate monthly energy consumption
                    monthly_data = link_power[link].abs().multiply(n.snapshot_weightings.generators, axis=0)
                    monthly_energy = monthly_data.resample('M').sum()
                    monthly_link_emissions = monthly_energy * emission_factor
                    
                    # Assigned to coal or natural gas based on carrier type
                    if 'coal' in carrier.lower():
                        for month_idx, month_emissions in monthly_link_emissions.items():
                            month_key = f"coal_emissions_{month_idx.strftime('%b').lower()}"
                            monthly_coal_emissions[month_key] = monthly_coal_emissions.get(month_key, 0.0) + month_emissions
                    elif 'gas' in carrier.lower():
                        for month_idx, month_emissions in monthly_link_emissions.items():
                            month_key = f"gas_emissions_{month_idx.strftime('%b').lower()}"
                            monthly_gas_emissions[month_key] = monthly_gas_emissions.get(month_key, 0.0) + month_emissions
    
    # Calculate total emissions
    total_coal_emissions = sum(monthly_coal_emissions.values())
    total_gas_emissions = sum(monthly_gas_emissions.values())
    
    # Total storage emissions
    emissions.at["total_coal_emissions", label] = total_coal_emissions
    emissions.at["total_gas_emissions", label] = total_gas_emissions
    
    # Store monthly coal emissions
    for month_key, month_value in monthly_coal_emissions.items():
        if month_key in emissions.index:
            emissions.at[month_key, label] = month_value
    
    # Storage monthly natural gas emissions
    for month_key, month_value in monthly_gas_emissions.items():
        if month_key in emissions.index:
            emissions.at[month_key, label] = month_value
    
    # Record summary information
    logger.debug(f"Emissions calculation for {label}:")
    logger.debug(f"  Total coal emissions: {total_coal_emissions/1e6:.2f} million tonnes CO2")
    logger.debug(f"  Total gas emissions: {total_gas_emissions/1e6:.2f} million tonnes CO2")
    
    return emissions


def make_summaries(networks_dict, config=None):
    outputs = [
        "nodal_costs",
        "nodal_capacities",
        "nodal_cfs",
        "cfs",
        "costs",
        "capacities",
        "curtailment",
        "energy",
        "supply",
        "supply_energy",
        "prices",
        "weighted_prices",
        "price_statistics",
        "market_values",
        "metrics",
        "emissions",  # Add emissions calculation
    ]
    
    # Add aluminum statistics if add_aluminum is True
    if config and config.get("add_aluminum", False):
        outputs.append("aluminum_statistics")

    columns = pd.MultiIndex.from_tuples(
        networks_dict.keys(), names=["pathway", "planning_horizons"]
    )

    df = {}

    for output in outputs:
        df[output] = pd.DataFrame(columns=columns, dtype=float)

    for label, filename in networks_dict.items():
        logger.debug(f"Make summary for scenario {label}, using {filename}")

        n = pypsa.Network(filename)

        # Add config to network object for access in cost calculations
        if config is not None:
            n.config = config

        assign_carriers(n)
        assign_locations(n)

        for output in outputs:
            df[output] = globals()["calculate_" + output](n, label, df[output])

    return df

if __name__ == "__main__":
    if 'snakemake' not in globals():
        from _helpers import mock_snakemake
        snakemake = mock_snakemake('make_summary',
                                   opts='ll',
                                   topology ='current+Neighbor',
                                   pathway ='exponential175',
                                   planning_horizons=["2020"])

    logging.basicConfig(level=snakemake.config["logging"]["level"])
    config = snakemake.config
    wildcards = snakemake.wildcards


    def expand_from_wildcard(key, config):
        w = getattr(wildcards, key)
        return config["scenario"][key] if w == "all" else [w]


    networks_dict = {(pathway, planning_horizons): "results/version-"
                                                   + config["version"]
                                                   + f"/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc"
                     for opts in expand_from_wildcard("opts", config)
                     for planning_horizons in expand_from_wildcard("planning_horizons", config)
                     for pathway in expand_from_wildcard("pathway", config)
                     for topology in expand_from_wildcard("topology", config)
                     for heating_demand in expand_from_wildcard("heating_demand", config)}

    df = make_summaries(networks_dict, config)
    df["metrics"].loc["total costs"] = df["costs"].sum()


    def to_csv(dfs, dir):
        os.makedirs(dir, exist_ok=True)
        for key, df in dfs.items():
            df.to_csv(os.path.join(dir, f"{key}.csv"))


    to_csv(df, snakemake.output[0])