# SPDX-FileCopyrightText: : 2022 The PyPSA-China Authors, 2025 Ruike Lyu, rl8728@princeton.edu
#
# SPDX-License-Identifier: MIT

"""
Plots energy and cost summaries for solved networks.

This script provides visualization tools for analyzing energy system optimization results.
It creates stacked bar plots showing:
1. System costs by technology type
2. Energy production/consumption by technology type

Relevant Settings
-----------------
- plotting.costs_plots_threshold: Minimum cost threshold for including technologies in cost plots
- plotting.energy_threshold: Minimum energy threshold for including technologies in energy plots
- plotting.costs_max: Maximum value for cost plot y-axis
- plotting.energy_min/max: Y-axis limits for energy plots
- plotting.tech_colors: Color mapping for different technologies

Inputs
------
- costs.csv: CSV file containing cost data by technology
- energy.csv: CSV file containing energy data by technology

Outputs
-------
- costs.pdf: Plot showing system costs by technology
- energy.pdf: Plot showing energy production/consumption by technology

Description
-----------
The script processes cost and energy data from optimization results and creates
stacked bar plots to visualize the contribution of different technologies to
system costs and energy flows. It includes functions for renaming and categorizing
technologies, and handles data aggregation and visualization.
"""

import os
import logging
from _helpers import configure_logging

import pandas as pd
import matplotlib.pyplot as plt
plt.style.use("ggplot")

logger = logging.getLogger(__name__)

# Function to standardize technology names for consistent plotting
def rename_techs(label):
    # List of prefixes to remove from technology names
    prefix_to_remove = [
        "central ",
        "decentral ",
    ]

    # Dictionary for specific technology name replacements
    rename_if_contains_dict = {
        "water tanks": "hot water storage",
        "H2": "H2",
        "coal cc": "CC"
    }

    # List of technologies to rename if they contain these strings
    rename_if_contains = [
        "gas",
        "coal"
    ]

    # Direct mapping of technology names
    rename = {
        "solar": "solar PV",
        "Sabatier": "methanation",
        "offwind": "offshore wind",
        "onwind": "onshore wind",
        "ror": "hydroelectricity",
        "hydro": "hydroelectricity",
        "PHS": "hydroelectricity",
        "hydro_inflow": "hydroelectricity",
        "stations": "hydroelectricity",
        "AC": "transmission lines",
        "CO2 capture": "biomass carbon capture",
        "CC": "coal carbon capture",
        "battery": "battery"
    }

    # Remove prefixes from technology names
    for ptr in prefix_to_remove:
        if label[: len(ptr)] == ptr:
            label = label[len(ptr) :]

    # Apply specific replacements
    for old, new in rename_if_contains_dict.items():
        if old in label:
            label = new

    # Apply general replacements
    for rif in rename_if_contains:
        if rif in label:
            label = rif

    # Apply direct name mappings
    for old, new in rename.items():
        if old == label:
            label = new
    return label

# Define preferred order of technologies in plots
preferred_order = pd.Index(
    [
        "transmission lines",
        "hydroelectricity",
        "nuclear",
        "coal",
        "coal carbon capture",
        "coal power plant",
        "coal power plant retrofit",
        "coal boiler",
        "CHP coal",
        "gas",
        "OCGT",
        "gas boiler",
        "CHP gas",
        "biomass",
        "onshore wind",
        "offshore wind",
        "solar PV",
        "solar thermal",
        "heat pump",
        "resistive heater",
        "methanation",
        "H2",
        "H2 fuel cell",
        "H2 CHP",
        "battery",
        "battery storage",
        "hot water storage",
        "hydrogen storage",
        "aluminum",
        "aluminum smelter",
        "aluminum storage",
    ]
)

def plot_costs(infn, config, fn=None):
    """
    Create a stacked bar plot of system costs by technology.
    
    Parameters:
    -----------
    infn : str
        Path to input CSV file containing cost data
    config : dict
        Configuration dictionary containing plotting parameters
    fn : str, optional
        Path to save the output plot
    """
    # Read cost data from CSV file
    cost_df = pd.read_csv(infn,index_col=list(range(3)),header=[1])

    # Aggregate costs by technology
    df = cost_df.groupby(cost_df.index.get_level_values(2)).sum()

    # Convert to billions
    df = df/1e9

    # Rename technologies for consistent plotting
    df = df.groupby(df.index.map(rename_techs)).sum()
    
    # Filter out aluminum if add_aluminum is False
    if not config.get("add_aluminum", False):
        df = df[~df.index.str.contains("aluminum", case=False, na=False)]

    # Remove technologies with costs below threshold
    to_drop = df.index[df.max(axis=1) < config['plotting']['costs_plots_threshold']]

    print("dropping")
    print(df.loc[to_drop])
    df = df.drop(to_drop)
    print(df.sum())

    # Reorder technologies according to preferred order
    new_index = preferred_order.intersection(df.index).append(
        df.index.difference(preferred_order)
    )

    # Sort columns by total cost
    new_columns = df.sum().sort_values().index

    # Create plot
    fig, ax = plt.subplots()
    fig.set_size_inches((12,8))

    # Filter out technologies that don't have color configuration
    available_colors = [i for i in new_index if i in config['plotting']['tech_colors']]
    df_filtered = df.loc[available_colors, new_columns]
    
    # Plot stacked bars
    df_filtered.T.plot(
        kind="bar",
        ax=ax,
        stacked=True,
        color=[config['plotting']['tech_colors'][i] for i in available_colors],
    )

    # Format legend
    handles,labels = ax.get_legend_handles_labels()
    handles.reverse()
    labels.reverse()

    # Set plot limits and labels
    ax.set_ylim([0,config['plotting']['costs_max']])
    ax.set_ylabel("System Cost [EUR billion per year]")
    ax.set_xlabel("")
    ax.grid(axis="y")
    ax.legend(handles,labels,ncol=4,bbox_to_anchor=[1, 1],loc="upper left")

    fig.tight_layout()

    # Save plot if filename provided
    if fn is not None:
        fig.savefig(fn, transparent=True)

def plot_energy(infn, config, fn=None):
    """
    Create a stacked bar plot of energy production/consumption by technology.
    
    Parameters:
    -----------
    infn : str
        Path to input CSV file containing energy data
    config : dict
        Configuration dictionary containing plotting parameters
    fn : str, optional
        Path to save the output plot
    """
    # Read energy data from CSV file
    energy_df = pd.read_csv(infn, index_col=list(range(2)),header=[1])

    # Aggregate energy by technology
    df = energy_df.groupby(energy_df.index.get_level_values(1)).sum()

    # Convert MWh to TWh
    df = df/1e6

    # Rename technologies for consistent plotting
    df = df.groupby(df.index.map(rename_techs)).sum()
    
    # Filter out aluminum if add_aluminum is False
    if not config.get("add_aluminum", False):
        df = df[~df.index.str.contains("aluminum", case=False, na=False)]

    # Remove technologies with energy below threshold
    to_drop = df.index[df.abs().max(axis=1) < config['plotting']['energy_threshold']]

    logger.info(
        f"Dropping all technology with energy consumption or production below {config['plotting']['energy_threshold']} TWh/a"
    )
    logger.debug(df.loc[to_drop])
    df = df.drop(to_drop)

    logger.info(f"Total energy of {round(df.sum().iloc[0])} TWh/a")

    # Reorder technologies according to preferred order
    new_index = preferred_order.intersection(df.index).append(
        df.index.difference(preferred_order)
    )

    # Sort columns
    new_columns = df.columns.sort_values()

    # Create plot
    fig, ax = plt.subplots()
    fig.set_size_inches((12,8))

    # Filter out technologies that don't have color configuration
    available_colors = [i for i in new_index if i in config['plotting']['tech_colors']]
    df_filtered = df.loc[available_colors, new_columns]
    
    logger.debug(df_filtered)

    # Plot stacked bars
    df_filtered.T.plot(
        kind="bar",
        ax=ax,
        stacked=True,
        color=[config['plotting']['tech_colors'][i] for i in available_colors],
    )

    # Format legend
    handles,labels = ax.get_legend_handles_labels()
    handles.reverse()
    labels.reverse()

    # Set plot limits and labels
    ax.set_ylim([config['plotting']['energy_min'], config['plotting']['energy_max']])
    ax.set_ylabel("Energy [TWh/a]")
    ax.set_xlabel("")
    ax.grid(axis="y")
    ax.legend(handles,labels,ncol=4,bbox_to_anchor=[1, 1],loc="upper left")

    fig.tight_layout()

    # Save plot if filename provided
    if fn is not None:
        fig.savefig(fn, transparent=True)

if __name__ == "__main__":
    # Set up snakemake environment
    if 'snakemake' not in globals():
        from _helpers import mock_snakemake
        snakemake = mock_snakemake('plot_summary',
                                   opts='ll',
                                   topology ='current+Neighbor',
                                   pathway ='exponential175',
                                   planning_horizons="2020")
    configure_logging(snakemake)

    # Extract configuration and paths
    config = snakemake.config
    wildcards = snakemake.wildcards
    logs = snakemake.log
    out = snakemake.output
    paths = snakemake.input

    # Generate both cost and energy plots
    Summary = ['energy', 'costs']
    summary_i = 0
    for summary in Summary:
        try:
            func = globals()[f"plot_{summary}"]
        except KeyError:
            raise RuntimeError(f"plotting function for {summary} has not been defined")
        func(os.path.join(paths[0], f"{summary}.csv"), config, out[summary_i])
        summary_i = summary_i + 1
