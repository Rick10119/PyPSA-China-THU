# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
"""
This script generates capacity factor plots for different energy resources in the PyPSA-China model.
It creates visualizations showing how capacity factors vary by month for:
- Renewable energy (solar, onwind, offwind, hydro)
- Conventional power (coal, gas, nuclear)
- Other resources (biomass, etc.)

The plots show monthly average capacity factors (p.u.) for each technology type.

In addition to figures, the script **also writes monthly statistics to CSV** via
`save_monthly_data_to_csv`:
- Output directory: `results/monthly_capacity_factors/`
- Filenames: `monthly_capacity_factors_<planning_horizon>[ _<province> ][ _vN ].csv`

These CSV files are intended as inputs for scripts such as:
- `scripts/plot_capacity_factors_from_csv.py`
- `scripts/fig_7_plot_compare_employment_scenarios.py` (expects _15p.csv and _non_flexible.csv)

Two-scenario mode (MMMU and MMMU_non_flexible):
  python scripts/plot_capacity_factors.py --network-mmmu <path_to_15p.nc> --network-mmmu-non-flexible <path_to_non_flexible.nc>
  writes results/monthly_capacity_factors/monthly_capacity_factors_2050_15p.csv and
  monthly_capacity_factors_2050_non_flexible.csv.
"""

from _helpers import configure_logging
import seaborn as sns
import pandas as pd
import pypsa
import matplotlib.pyplot as plt
import numpy as np

# -----------------------------------------------------------------------------
# Half-month aggregation: for each month, aggregate over first half (days 1-15)
# and second half (day 16 to end), then average to get the monthly value.
# Used for capacity factors (coal, gas, aluminum, etc.) and load factors.
# -----------------------------------------------------------------------------

def _add_month_half(df):
    """Add month and half columns to a DataFrame with a time index. half=1 first half (day<=15), half=2 second half. If no day attribute, half is 1 for all."""
    idx = df.index
    df = df.copy()
    df['month'] = idx.month
    if hasattr(idx, 'day'):
        df['half'] = (idx.day <= 15).astype(int) + 1
    else:
        df['half'] = 1
    return df


def monthly_from_half_month(series, agg='mean'):
    """
    Aggregate by half-month then average to get monthly values. Used for capacity factors, load factors, etc.
    series : pd.Series
        Time series with DatetimeIndex (e.g. power or capacity factor)
    agg : str
        Aggregation within each half-month: 'mean' or 'max'
    Returns
    -------
    pd.Series
        Index is month 1..12, values are the average of first-half and second-half aggregates for that month
    """
    if series.empty:
        return pd.Series(dtype=float)
    df = series.to_frame('x')
    df = _add_month_half(df)
    half_agg = df.groupby(['month', 'half'])['x'].agg(agg)
    return half_agg.groupby(level='month').mean()


def set_plot_style():
    """
    Sets up the plotting style for all matplotlib plots in this script.
    Uses a combination of classic and seaborn styles with custom modifications
    for better visualization quality.
    """
    plt.style.use(['classic', 'seaborn-v0_8-whitegrid',
                   {'axes.grid': False, 'grid.linestyle': '--', 'grid.color': u'0.6',
                    'hatch.color': 'white',
                    'patch.linewidth': 0.5,
                    'font.size': 20,
                    'legend.fontsize': 'large',
                    'lines.linewidth': 1.5,
                    'pdf.fonttype': 42,
                    }])

def filter_network_by_province(n, target_province=None):
    """
    Filter the network to include only components from a specific province.
    
    Parameters:
    -----------
    n : pypsa.Network
        The PyPSA network object containing the simulation results
    target_province : str, optional
        The target province name (e.g., 'Shandong'). If None, returns the full network.
    
    Returns:
    --------
    pypsa.Network
        Filtered network containing only components from the target province
    """
    if target_province is None:
        return n
    
    print(f"Filtering network to keep only components in province {target_province}...")
    
    # Create a copy of the network to avoid modifying the original
    n_filtered = n.copy()
    
    # Find buses in the target province
    province_buses = n_filtered.buses[n_filtered.buses.index.str.contains(target_province, case=False)].index
    
    if len(province_buses) == 0:
        print(f"Warning: No nodes found for province {target_province}")
        return n_filtered
    
    print(f"Found {len(province_buses)} nodes in province {target_province}: {list(province_buses)}")
    
    # Remove generators not in the target province
    non_province_generators = n_filtered.generators[~n_filtered.generators.bus.isin(province_buses)].index
    if len(non_province_generators) > 0:
        n_filtered.mremove("Generator", non_province_generators)
        print(f"Removed {len(non_province_generators)} generators outside province {target_province}")
    
    # Remove loads not in the target province
    non_province_loads = n_filtered.loads[~n_filtered.loads.bus.isin(province_buses)].index
    if len(non_province_loads) > 0:
        n_filtered.mremove("Load", non_province_loads)
        print(f"Removed {len(non_province_loads)} loads outside province {target_province}")
    
    # Remove storage units not in the target province
    non_province_storage = n_filtered.storage_units[~n_filtered.storage_units.bus.isin(province_buses)].index
    if len(non_province_storage) > 0:
        n_filtered.mremove("StorageUnit", non_province_storage)
        print(f"Removed {len(non_province_storage)} storage units outside province {target_province}")
    
    # Remove stores not in the target province
    non_province_stores = n_filtered.stores[~n_filtered.stores.bus.isin(province_buses)].index
    if len(non_province_stores) > 0:
        n_filtered.mremove("Store", non_province_stores)
        print(f"Removed {len(non_province_stores)} stores outside province {target_province}")
    
    # Remove links not connected to the target province
    non_province_links = n_filtered.links[~(n_filtered.links.bus0.isin(province_buses) | n_filtered.links.bus1.isin(province_buses))].index
    if len(non_province_links) > 0:
        n_filtered.mremove("Link", non_province_links)
        print(f"Removed {len(non_province_links)} links outside province {target_province}")
    
    # Remove lines not connected to the target province
    non_province_lines = n_filtered.lines[~(n_filtered.lines.bus0.isin(province_buses) | n_filtered.lines.bus1.isin(province_buses))].index
    if len(non_province_lines) > 0:
        n_filtered.mremove("Line", non_province_lines)
        print(f"Removed {len(non_province_lines)} lines outside province {target_province}")
    
    # Finally remove non-province buses
    non_province_buses = n_filtered.buses[~n_filtered.buses.index.isin(province_buses)].index
    if len(non_province_buses) > 0:
        n_filtered.mremove("Bus", non_province_buses)
        print(f"Removed {len(non_province_buses)} buses outside province {target_province}")
    
    print(f"Filter complete. Remaining: {len(n_filtered.generators)} generators, {len(n_filtered.loads)} loads, {len(n_filtered.links)} links")
    
    return n_filtered


def calculate_monthly_capacity_factors(n):
    """
    Calculate monthly average capacity factors for all generators and power-producing links in the network.
    Uses half-month stats (first/second half of month) then averages to get monthly; denominator = actual max power.
    
    Parameters:
    -----------
    n : pypsa.Network
        The PyPSA network object containing the simulation results
    
    Returns:
    --------
    dict
        Dictionary containing monthly capacity factors for different technology groups
    """
    if not hasattr(n, 'generators_t') or not hasattr(n.generators_t, 'p'):
        print("Warning: No generator time series data found")
        return {}
    
    # Get generator power output and calculate actual maximum power output
    gen_power = n.generators_t.p
    gen_max_power = gen_power.max()  # Use actual maximum power output as capacity
    
    # Get link power output and calculate actual maximum power output
    link_power = pd.DataFrame()
    link_max_power = pd.Series(dtype=float)
    
    if hasattr(n, 'links_t') and hasattr(n.links_t, 'p0'):
        # Filter links that produce electricity (bus1 is electricity bus)
        elec_links = n.links[n.links.bus1.isin(n.buses[n.buses.carrier == 'AC'].index)]
        if not elec_links.empty:
            link_power = n.links_t.p0[elec_links.index].copy()
            # Use actual maximum power output as capacity
            link_max_power = link_power.max()
        
        # Also include aluminum smelters even if they don't connect to AC bus
        aluminum_smelters = n.links[n.links.carrier == 'aluminum']
        if not aluminum_smelters.empty:
            # Add aluminum smelter power data if available
            for link in aluminum_smelters.index:
                if link in n.links_t.p0.columns:
                    if link not in link_power.columns:
                        link_power[link] = n.links_t.p0[link]
                    # Calculate actual maximum power output for aluminum smelters
                    if link not in link_max_power.index:
                        link_max_power[link] = n.links_t.p0[link].max()
            
            # Debug: Check aluminum smelter links specifically
            print("\nChecking aluminum smelter links:")
            aluminum_smelters = n.links[n.links.carrier == 'aluminum']
            if not aluminum_smelters.empty:
                print(f"Found {len(aluminum_smelters)} aluminum smelter links:")
                for link in aluminum_smelters.index:
                    max_power = link_max_power[link] if link in link_max_power.index else 0
                    print(f"  {link}: max power = {max_power:.2f} MW")
            else:
                print("No aluminum smelter links found in network")
                
            # Debug: Check all links with 'smelter' in name
            smelter_links = n.links[n.links.index.str.contains('smelter', case=False)]
            if not smelter_links.empty:
                print(f"\nFound {len(smelter_links)} links with 'smelter' in name:")
                for link in smelter_links.index:
                    carrier = smelter_links.at[link, 'carrier']
                    max_power = link_max_power[link] if link in link_max_power.index else 0
                    print(f"  {link}: carrier = {carrier}, max power = {max_power:.2f} MW")
            else:
                print("No links with 'smelter' in name found")
    
    # Debug: Print available generator carriers
    print("Available generator carriers:")
    carriers = n.generators.carrier.value_counts()
    for carrier, count in carriers.items():
        print(f"  {carrier}: {count} generators")
    
    # Debug: Check if aluminum smelters are modeled as generators
    print("\nChecking for aluminum smelters in generators:")
    aluminum_gens = n.generators[n.generators.index.str.contains('aluminum|smelter', case=False)]
    if not aluminum_gens.empty:
        print(f"Found {len(aluminum_gens)} aluminum-related generators:")
        for gen in aluminum_gens.index:
            carrier = aluminum_gens.at[gen, 'carrier']
            capacity = aluminum_gens.at[gen, 'p_nom_opt']
            print(f"  {gen}: carrier = {carrier}, capacity = {capacity:.2f} MW")
    else:
        print("No aluminum-related generators found")
    
    # Debug: Print generators with non-zero maximum power
    non_zero_max_power = gen_max_power[gen_max_power > 0]
    print(f"\nGenerators with non-zero maximum power: {len(non_zero_max_power)}")
    for gen in non_zero_max_power.index:
        carrier = n.generators.at[gen, 'carrier']
        max_power = non_zero_max_power[gen]
        print(f"  {gen} ({carrier}): {max_power:.2f} MW")
    
    # Define technology groups with more specific matching
    tech_groups = {
        'Hydro': ['hydro', 'hydroelectricity'],
        'Nuclear': ['nuclear'],
        'Coal': ['coal cc', 'CHP coal', 'coal power plant'],
        'Gas': ['OCGT gas', 'CHP gas'],
        'Wind': ['onwind', 'offwind', 'wind'],
        'Solar': ['solar', 'solar pv', 'pv'],
        'Aluminum': ['aluminum', 'smelter'],
        'Other': []  # Will catch any other technologies
    }
    
    # Debug: Print available link carriers
    if not link_power.empty:
        print("\nAvailable link carriers:")
        link_carriers = n.links[n.links.index.isin(link_power.columns)].carrier.value_counts()
        for carrier, count in link_carriers.items():
            print(f"  {carrier}: {count} links")
        
        # Debug: Print all links that might be aluminum smelters
        print("\nLinks that might be aluminum smelters:")
        for link in link_power.columns:
            link_name = link
            carrier = n.links.at[link, 'carrier']
            print(f"  {link_name}: carrier = {carrier}")
    else:
        print("\nNo link power data available")
    
    # Debug: Check all links in network (including those not in link_power)
    print("\nAll links in network:")
    all_links = n.links.carrier.value_counts()
    for carrier, count in all_links.items():
        print(f"  {carrier}: {count} links")
    
    # Debug: Check aluminum-related links in entire network
    print("\nAll aluminum-related links in network:")
    all_aluminum_links = n.links[n.links.index.str.contains('aluminum|smelter', case=False)]
    if not all_aluminum_links.empty:
        for link in all_aluminum_links.index:
            carrier = all_aluminum_links.at[link, 'carrier']
            capacity = all_aluminum_links.at[link, 'p_nom_opt']
            in_power_data = link in link_power.columns if not link_power.empty else False
            print(f"  {link}: carrier = {carrier}, capacity = {capacity:.2f} MW, in power data = {in_power_data}")
    else:
        print("No aluminum-related links found in entire network")
    
    monthly_cf = {}
    
    for group_name, carriers in tech_groups.items():
        # Find generators and links belonging to this group
        if carriers:
            # Filter generators by carrier with flexible matching
            group_generators = []
            group_links = []
            
            for carrier in carriers:
                # Try exact matching first for generators
                exact_matches = n.generators[n.generators.carrier == carrier].index.tolist()
                # Exclude fuel generators
                exact_matches = [gen for gen in exact_matches if 'fuel' not in gen.lower()]
                group_generators.extend(exact_matches)
                
                # If no exact matches, try partial matching for generators
                if not exact_matches:
                    partial_matches = n.generators[n.generators.carrier.str.contains(carrier, case=False, na=False)].index.tolist()
                    # Exclude fuel generators
                    partial_matches = [gen for gen in partial_matches if 'fuel' not in gen.lower()]
                    group_generators.extend(partial_matches)
                
                # Try exact matching for links
                if not link_power.empty:
                    exact_link_matches = n.links[n.links.carrier == carrier].index.tolist()
                    # Only include links that produce electricity
                    exact_link_matches = [link for link in exact_link_matches if link in link_power.columns]
                    group_links.extend(exact_link_matches)
                    
                    # If no exact matches, try partial matching for links
                    if not exact_link_matches:
                        partial_link_matches = n.links[n.links.carrier.str.contains(carrier, case=False, na=False)].index.tolist()
                        # Only include links that produce electricity
                        partial_link_matches = [link for link in partial_link_matches if link in link_power.columns]
                        group_links.extend(partial_link_matches)
                    
                    # Special handling for aluminum smelters - also check link names
                    if carrier == 'aluminum' or carrier == 'smelter':
                        smelter_links = [link for link in link_power.columns if 'smelter' in link.lower()]
                        group_links.extend(smelter_links)
                        # Also check for exact carrier match
                        if carrier == 'aluminum':
                            aluminum_links = [link for link in link_power.columns if n.links.at[link, 'carrier'] == 'aluminum']
                            group_links.extend(aluminum_links)
        else:
            # For 'Other' group, include all generators and links not in other groups
            all_used_generators = set()
            all_used_links = set()
            
            for carriers_list in tech_groups.values():
                if carriers_list:  # Skip empty list for 'Other'
                    for carrier in carriers_list:
                        # Generators
                        exact_matches = n.generators[n.generators.carrier == carrier].index.tolist()
                        all_used_generators.update(exact_matches)
                        if not exact_matches:
                            partial_matches = n.generators[n.generators.carrier.str.contains(carrier, case=False, na=False)].index.tolist()
                            all_used_generators.update(partial_matches)
                        
                        # Links
                        if not link_power.empty:
                            exact_link_matches = n.links[n.links.carrier == carrier].index.tolist()
                            exact_link_matches = [link for link in exact_link_matches if link in link_power.columns]
                            all_used_links.update(exact_link_matches)
                            if not exact_link_matches:
                                partial_link_matches = n.links[n.links.carrier.str.contains(carrier, case=False, na=False)].index.tolist()
                                partial_link_matches = [link for link in partial_link_matches if link in link_power.columns]
                                all_used_links.update(partial_link_matches)
            
            # Exclude fuel generators from Other group
            group_generators = [gen for gen in n.generators.index if gen not in all_used_generators and 'fuel' not in gen.lower()]
            group_links = [link for link in link_power.columns if link not in all_used_links and 'smelter' not in link.lower()]
        
        # Calculate capacity factors for this group (generators + links)
        total_power = pd.Series(0, index=gen_power.index)
        total_max_power = 0
        
        if group_generators:
            total_power += gen_power[group_generators].sum(axis=1)
            total_max_power += gen_max_power[group_generators].sum()
        
        if group_links:
            total_power += link_power[group_links].sum(axis=1)
            total_max_power += link_max_power[group_links].sum()
        
        if total_max_power > 0:
            # Same half-month-then-average logic for all (coal, gas, aluminum, etc.)
            if group_name in ('Coal', 'Gas'):
                annual_max_power = total_power.max()
                if annual_max_power > 0:
                    monthly_max_power = monthly_from_half_month(total_power, agg='max')
                    monthly_cf[group_name] = monthly_max_power / annual_max_power
            else:
                cf = total_power / total_max_power
                monthly_cf[group_name] = monthly_from_half_month(cf, agg='mean')
            
            # Debug print
            print(f"Debug - {group_name}: {len(group_generators)} generators, {len(group_links)} links, max power: {total_max_power:.2f} MW, actual max power: {total_power.max():.2f} MW")
            if group_generators:
                print(f"  Generators: {group_generators[:3]}...")  # Show first 3 generators
            if group_links:
                print(f"  Links: {group_links[:3]}...")  # Show first 3 links
                if group_name == 'Aluminum':
                    print(f"  All aluminum links: {group_links}")
    
    return monthly_cf

def calculate_monthly_max_capacity_factors(n):
    """
    Calculate monthly maximum capacity factors for all generators and power-producing links in the network.
    Monthly max capacity factor = monthly maximum power output / annual maximum power output
    
    Parameters:
    -----------
    n : pypsa.Network
        The PyPSA network object containing the simulation results
    
    Returns:
    --------
    dict
        Dictionary containing monthly maximum capacity factors for different technology groups
    """
    if not hasattr(n, 'generators_t') or not hasattr(n.generators_t, 'p'):
        print("Warning: No generator time series data found")
        return {}
    
    # Get generator power output and calculate actual maximum power output
    gen_power = n.generators_t.p
    gen_max_power = gen_power.max()  # Use actual maximum power output as capacity
    
    # Get link power output and calculate actual maximum power output
    link_power = pd.DataFrame()
    link_max_power = pd.Series()
    
    if hasattr(n, 'links_t') and hasattr(n.links_t, 'p0'):
        # Filter links that produce electricity (bus1 is electricity bus)
        elec_links = n.links[n.links.bus1.isin(n.buses[n.buses.carrier == 'AC'].index)]
        if not elec_links.empty:
            link_power = n.links_t.p0[elec_links.index]
            # Use actual maximum power output as capacity
            link_max_power = link_power.max()
        
        # Also include aluminum smelters even if they don't connect to AC bus
        aluminum_smelters = n.links[n.links.carrier == 'aluminum']
        if not aluminum_smelters.empty:
            # Add aluminum smelter power data if available
            for link in aluminum_smelters.index:
                if link in n.links_t.p0.columns:
                    if link not in link_power.columns:
                        link_power[link] = n.links_t.p0[link]
                    # Calculate actual maximum power output for aluminum smelters
                    if link not in link_max_power.index:
                        link_max_power[link] = n.links_t.p0[link].max()
    
    # Define technology groups with more specific matching
    tech_groups = {
        'Hydro': ['hydro', 'hydroelectricity'],
        'Nuclear': ['nuclear'],
        'Coal': ['coal cc', 'CHP coal', 'coal power plant'],
        'Gas': ['OCGT gas', 'CHP gas'],
        'Wind': ['onwind', 'offwind', 'wind'],
        'Solar': ['solar', 'solar pv', 'pv'],
        'Aluminum': ['aluminum', 'smelter'],
        'Other': []  # Will catch any other technologies
    }
    
    monthly_max_cf = {}
    
    for group_name, carriers in tech_groups.items():
        # Find generators and links belonging to this group
        if carriers:
            # Filter generators by carrier with flexible matching
            group_generators = []
            group_links = []
            
            for carrier in carriers:
                # Try exact matching first for generators
                exact_matches = n.generators[n.generators.carrier == carrier].index.tolist()
                # Exclude fuel generators
                exact_matches = [gen for gen in exact_matches if 'fuel' not in gen.lower()]
                group_generators.extend(exact_matches)
                
                # If no exact matches, try partial matching for generators
                if not exact_matches:
                    partial_matches = n.generators[n.generators.carrier.str.contains(carrier, case=False, na=False)].index.tolist()
                    # Exclude fuel generators
                    partial_matches = [gen for gen in partial_matches if 'fuel' not in gen.lower()]
                    group_generators.extend(partial_matches)
                
                # Try exact matching for links
                if not link_power.empty:
                    exact_link_matches = n.links[n.links.carrier == carrier].index.tolist()
                    # Only include links that produce electricity
                    exact_link_matches = [link for link in exact_link_matches if link in link_power.columns]
                    group_links.extend(exact_link_matches)
                    
                    # If no exact matches, try partial matching for links
                    if not exact_link_matches:
                        partial_link_matches = n.links[n.links.carrier.str.contains(carrier, case=False, na=False)].index.tolist()
                        # Only include links that produce electricity
                        partial_link_matches = [link for link in partial_link_matches if link in link_power.columns]
                        group_links.extend(partial_link_matches)
                    
                    # Special handling for aluminum smelters - also check link names
                    if carrier == 'aluminum' or carrier == 'smelter':
                        smelter_links = [link for link in link_power.columns if 'smelter' in link.lower()]
                        group_links.extend(smelter_links)
                        # Also check for exact carrier match
                        if carrier == 'aluminum':
                            aluminum_links = [link for link in link_power.columns if n.links.at[link, 'carrier'] == 'aluminum']
                            group_links.extend(aluminum_links)
        else:
            # For 'Other' group, include all generators and links not in other groups
            all_used_generators = set()
            all_used_links = set()
            
            for carriers_list in tech_groups.values():
                if carriers_list:  # Skip empty list for 'Other'
                    for carrier in carriers_list:
                        # Generators
                        exact_matches = n.generators[n.generators.carrier == carrier].index.tolist()
                        all_used_generators.update(exact_matches)
                        if not exact_matches:
                            partial_matches = n.generators[n.generators.carrier.str.contains(carrier, case=False, na=False)].index.tolist()
                            all_used_generators.update(partial_matches)
                        
                        # Links
                        if not link_power.empty:
                            exact_link_matches = n.links[n.links.carrier == carrier].index.tolist()
                            exact_link_matches = [link for link in exact_link_matches if link in link_power.columns]
                            all_used_links.update(exact_link_matches)
                            if not exact_link_matches:
                                partial_link_matches = n.links[n.links.carrier.str.contains(carrier, case=False, na=False)].index.tolist()
                                partial_link_matches = [link for link in partial_link_matches if link in link_power.columns]
                                all_used_links.update(partial_link_matches)
            
            # Exclude fuel generators from Other group
            group_generators = [gen for gen in n.generators.index if gen not in all_used_generators and 'fuel' not in gen.lower()]
            group_links = [link for link in link_power.columns if link not in all_used_links and 'smelter' not in link.lower()]
        
        # Calculate capacity factors for this group (generators + links)
        total_power = pd.Series(0, index=gen_power.index)
        total_max_power = 0
        
        if group_generators:
            total_power += gen_power[group_generators].sum(axis=1)
            total_max_power += gen_max_power[group_generators].sum()
        
        if group_links:
            total_power += link_power[group_links].sum(axis=1)
            total_max_power += link_max_power[group_links].sum()
        
        if total_max_power > 0:
            # Same half-month-then-average logic
            if group_name in ('Coal', 'Gas'):
                annual_max_power = total_power.max()
                if annual_max_power > 0:
                    monthly_max_power = monthly_from_half_month(total_power, agg='max')
                    monthly_max_cf[group_name] = monthly_max_power / annual_max_power
            else:
                cf = total_power / total_max_power
                monthly_max_cf[group_name] = monthly_from_half_month(cf, agg='max')
    
    return monthly_max_cf

def calculate_monthly_load_factors(n):
    """
    Calculate monthly average load factors for electricity and heating loads.
    
    Parameters:
    -----------
    n : pypsa.Network
        The PyPSA network object containing the simulation results
    
    Returns:
    --------
    dict
        Dictionary containing monthly load factors for different load types
    """
    monthly_load = {}
    
    # Calculate electricity load factors
    if hasattr(n, 'loads_t') and hasattr(n.loads_t, 'p_set'):
        # Get electricity loads (excluding heating and aluminum loads)
        elec_loads = n.loads_t.p_set.filter(regex='^(?!.*(heat|aluminum)).*$', axis=1)
        
        if not elec_loads.empty:
            # Calculate total electricity load
            total_elec_load = elec_loads.sum(axis=1)
            max_elec_load = total_elec_load.max()
            
            if max_elec_load > 0:
                elec_load_factor = total_elec_load / max_elec_load
                monthly_load['Electricity Load'] = monthly_from_half_month(elec_load_factor, agg='mean')
    
    # Calculate heating load factors
    if hasattr(n, 'loads_t') and hasattr(n.loads_t, 'p_set'):
        # Get heating loads
        heat_loads = n.loads_t.p_set.filter(like='heat')
        
        if not heat_loads.empty:
            # Calculate total heating load
            total_heat_load = heat_loads.sum(axis=1)
            max_heat_load = total_heat_load.max()
            
            if max_heat_load > 0:
                heat_load_factor = total_heat_load / max_heat_load
                monthly_load['Heating Load'] = monthly_from_half_month(heat_load_factor, agg='mean')
    
    # Calculate aluminum load factors if available
    if hasattr(n, 'loads_t') and hasattr(n.loads_t, 'p_set'):
        # Get aluminum loads
        aluminum_loads = n.loads_t.p_set.filter(like='aluminum')
        
        if not aluminum_loads.empty:
            # Calculate total aluminum load
            total_aluminum_load = aluminum_loads.sum(axis=1)
            max_aluminum_load = total_aluminum_load.max()
            
            if max_aluminum_load > 0:
                aluminum_load_factor = total_aluminum_load / max_aluminum_load
                monthly_load['Aluminum Load'] = monthly_from_half_month(aluminum_load_factor, agg='mean')
    
    return monthly_load

def save_monthly_data_to_csv(monthly_cf, monthly_max_cf, monthly_load, planning_horizon, target_province=None, scenario_suffix=None):
    """
    Save monthly capacity factors, monthly maximum capacity factors, and load factors to CSV.
    If scenario_suffix is set (e.g. '15p' or 'non_flexible'), filename is
    monthly_capacity_factors_<planning_horizon>[ _<province>]_<scenario_suffix>.csv
    and the file is overwritten. Otherwise, if the base file exists, a versioned file is created.
    
    Parameters
    ----------
    monthly_cf : dict
        Monthly average capacity factors by technology
    monthly_max_cf : dict
        Monthly maximum capacity factors by technology
    monthly_load : dict
        Monthly load factors by load type
    planning_horizon : str
        Planning horizon (e.g. '2050')
    target_province : str, optional
        Province filter for filename (e.g. 'Shandong')
    scenario_suffix : str, optional
        Scenario label for two-scenario output (e.g. '15p', 'non_flexible'). When set,
        the CSV is named with this suffix and no version number is used.
    """
    import os
    import glob
    
    output_dir = "results/monthly_capacity_factors"
    os.makedirs(output_dir, exist_ok=True)
    
    filename_suffix = f"_{target_province}" if target_province else ""
    base_filename = f"monthly_capacity_factors_{planning_horizon}{filename_suffix}"
    
    if scenario_suffix:
        csv_filename = f"{output_dir}/{base_filename}_{scenario_suffix}.csv"
        print(f"Writing scenario CSV: {base_filename}_{scenario_suffix}.csv")
    else:
        csv_filename = f"{output_dir}/{base_filename}.csv"
        version = 1
        if os.path.exists(csv_filename):
            pattern = f"{output_dir}/{base_filename}_v*.csv"
            existing_files = glob.glob(pattern)
            if existing_files:
                versions = []
                for file in existing_files:
                    try:
                        version_part = file.split('_v')[-1].split('.csv')[0]
                        if version_part.isdigit():
                            versions.append(int(version_part))
                    except (IndexError, ValueError):
                        continue
                version = max(versions) + 1 if versions else 2
            else:
                version = 2
            csv_filename = f"{output_dir}/{base_filename}_v{version}.csv"
            print(f"File {base_filename}.csv exists; writing {base_filename}_v{version}.csv")
        else:
            print(f"Writing: {base_filename}.csv")
    
    # Combine all data into a single DataFrame
    all_data = {}
    
    # Add average capacity factors
    for tech, cf_data in monthly_cf.items():
        if not cf_data.empty:
            all_data[f"{tech}_Capacity_Factor_Avg"] = cf_data
    
    # Add maximum capacity factors
    for tech, max_cf_data in monthly_max_cf.items():
        if not max_cf_data.empty:
            all_data[f"{tech}_Capacity_Factor_Max"] = max_cf_data
    
    # Add load factors
    for load_type, load_data in monthly_load.items():
        if not load_data.empty:
            all_data[f"{load_type}_Load_Factor"] = load_data
    
    if all_data:
        # Create DataFrame
        df = pd.DataFrame(all_data)
        df.index.name = 'Month'
        
        # Add month names
        month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        df['Month_Name'] = [month_names[i-1] for i in df.index]
        
        # Reorder columns to put Month_Name first
        cols = ['Month_Name'] + [col for col in df.columns if col != 'Month_Name']
        df = df[cols]
        
        # Save to CSV
        df.to_csv(csv_filename, index=True)
        print(f"\nMonthly capacity factor and load factor data saved to: {csv_filename}")
        
        # Print summary of saved data
        print(f"Saved data includes:")
        for col in df.columns:
            if col != 'Month_Name':
                print(f"  - {col}")
    else:
        print("Warning: No data available to save")


def run_one_scenario_to_csv(network_path, planning_horizon="2050", target_province=None, scenario_suffix=None):
    """
    Load one network, compute monthly capacity/load factors, and save to CSV.
    Used for the two-scenario (MMMU and MMMU_non_flexible) CSV output.
    
    Parameters
    ----------
    network_path : str
        Path to PyPSA network .nc file
    planning_horizon : str
        Planning horizon for filename (default 2050)
    target_province : str, optional
        If set, filter network to this province before computing
    scenario_suffix : str, optional
        Suffix for CSV filename (e.g. '15p', 'non_flexible')
    
    Returns
    -------
    str or None
        Path to saved CSV, or None if no data
    """
    import os
    if not os.path.exists(network_path):
        print(f"Error: Network file not found: {network_path}")
        return None
    n = pypsa.Network(network_path)
    if target_province:
        n = filter_network_by_province(n, target_province)
    monthly_cf = calculate_monthly_capacity_factors(n)
    monthly_max_cf = calculate_monthly_max_capacity_factors(n)
    monthly_load = calculate_monthly_load_factors(n)
    # In non_flexible scenario aluminum is non-dispatchable: capacity factor = 1 (add key if missing so CSV is consistent)
    if scenario_suffix == "non_flexible":
        month_index = pd.RangeIndex(1, 13, name="month")
        ones = pd.Series(1.0, index=month_index)
        monthly_cf["Aluminum"] = ones
        monthly_max_cf["Aluminum"] = ones
    if not monthly_cf and not monthly_load:
        print(f"Warning: No capacity/load data for {scenario_suffix or 'scenario'}")
        return None
    save_monthly_data_to_csv(
        monthly_cf, monthly_max_cf, monthly_load,
        planning_horizon, target_province, scenario_suffix=scenario_suffix
    )
    output_dir = "results/monthly_capacity_factors"
    filename_suffix = f"_{target_province}" if target_province else ""
    base = f"monthly_capacity_factors_{planning_horizon}{filename_suffix}"
    return f"{output_dir}/{base}_{scenario_suffix}.csv" if scenario_suffix else f"{output_dir}/{base}.csv"


def run_two_scenarios_to_csv(network_path_15p, network_path_non_flexible, planning_horizon="2050", target_province=None):
    """
    Run capacity factor extraction for MMMU (15p) and MMMU_non_flexible,
    saving two CSVs: ..._15p.csv and ..._non_flexible.csv.
    These match the inputs expected by fig_7_plot_compare_employment_scenarios.
    
    Parameters
    ----------
    network_path_15p : str
        Path to MMMU scenario network (e.g. version-*-MMMU-2050-15p/.../postnetwork-*.nc)
    network_path_non_flexible : str
        Path to MMMU_non_flexible scenario network (e.g. version-*-MMMU-2050-non_flexible/.../postnetwork-*.nc)
    planning_horizon : str
        Planning horizon (default 2050)
    target_province : str, optional
        Optional province filter
    """
    print("Scenario 1 (MMMU, 15p):")
    run_one_scenario_to_csv(network_path_15p, planning_horizon, target_province, scenario_suffix="15p")
    print("\nScenario 2 (MMMU_non_flexible):")
    run_one_scenario_to_csv(network_path_non_flexible, planning_horizon, target_province, scenario_suffix="non_flexible")


def plot_capacity_factors(n, config, target_province=None):
    """
    Generate capacity factor plots for all energy resources.
    
    Parameters:
    -----------
    n : pypsa.Network
        The PyPSA network object containing the simulation results
    config : dict
        Configuration dictionary containing plotting parameters
    target_province : str, optional
        The target province name to filter results (e.g., 'Shandong')
    """
    planning_horizon = snakemake.wildcards.planning_horizons
    
    # Filter network by province if specified
    if target_province:
        n = filter_network_by_province(n, target_province)
    
    # Calculate monthly capacity factors
    monthly_cf = calculate_monthly_capacity_factors(n)
    monthly_max_cf = calculate_monthly_max_capacity_factors(n)
    monthly_load = calculate_monthly_load_factors(n)
    
    if not monthly_cf and not monthly_load:
        print("Warning: No capacity factor or load data available")
        return
    
    # Create a single plot
    fig, ax = plt.subplots(1, 1, figsize=(12, 8))
    
    # Define colors for different technologies
    colors = {
        'Hydro': '#000080',      # Navy
        'Nuclear': '#800080',    # Purple
        'Coal': '#000000',       # Black
        'Gas': '#FF0000',        # Red
        'Wind': '#00BFFF',       # Deep sky blue
        'Solar': '#FFD700',      # Gold
        'Aluminum': '#FF69B4',   # Hot pink
        'Other': '#808080'       # Gray
    }
    
    # Define colors for loads
    load_colors = {
        'Electricity Load': '#1f77b4',    # Blue
        'Heating Load': '#ff7f0e',        # Orange
        'Aluminum Load': '#2ca02c'        # Green
    }
    
    # Plot all capacity factors in one graph
    all_techs = ['Hydro', 'Nuclear', 'Coal', 'Gas', 'Wind', 'Solar', 'Aluminum', 'Other']
    for tech in all_techs:
        if tech in monthly_cf:
            months = monthly_cf[tech].index
            values = monthly_cf[tech].values
            ax.plot(months, values, 'o-', color=colors.get(tech, '#000000'), 
                    linewidth=2, markersize=6, label=f'{tech} (Avg)')
        
        # Plot monthly maximum capacity factors
        if tech in monthly_max_cf:
            months = monthly_max_cf[tech].index
            values = monthly_max_cf[tech].values
            ax.plot(months, values, 's--', color=colors.get(tech, '#000000'), 
                    linewidth=2, markersize=6, label=f'{tech} (Max)', alpha=0.7)
    
    # Plot load factors
    for load_type in ['Electricity Load', 'Heating Load', 'Aluminum Load']:
        if load_type in monthly_load:
            months = monthly_load[load_type].index
            values = monthly_load[load_type].values
            ax.plot(months, values, 's--', color=load_colors.get(load_type, '#000000'), 
                    linewidth=2, markersize=6, label=load_type)
    
    ax.set_ylabel('Capacity/Load Factor (p.u.)', fontsize=20)
    ax.set_xlabel('Month', fontsize=20)
    ax.set_title('Monthly Capacity Factors (Avg & Max) & Load Factors', fontsize=14, fontweight='bold')
    ax.set_xlim(1, 12)
    ax.set_ylim(0, 1.0)
    ax.set_xticks(range(1, 13))
    ax.set_xticklabels(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                         'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best', ncol=2)
    
    # Add value labels for all technologies and loads
    for tech in all_techs:
        if tech in monthly_cf:
            months = monthly_cf[tech].index
            values = monthly_cf[tech].values
            for month, value in zip(months, values):
                ax.annotate(f'{value:.2f}', (month, value), 
                           textcoords="offset points", xytext=(0,10), 
                           ha='center', fontsize=8)
        
        # Add value labels for monthly maximum capacity factors
        if tech in monthly_max_cf:
            months = monthly_max_cf[tech].index
            values = monthly_max_cf[tech].values
            for month, value in zip(months, values):
                ax.annotate(f'{value:.2f}', (month, value), 
                           textcoords="offset points", xytext=(0,-15), 
                           ha='center', fontsize=8, alpha=0.7)
    
    for load_type in ['Electricity Load', 'Heating Load']:
        if load_type in monthly_load:
            months = monthly_load[load_type].index
            values = monthly_load[load_type].values
            for month, value in zip(months, values):
                ax.annotate(f'{value:.2f}', (month, value), 
                           textcoords="offset points", xytext=(0,10), 
                           ha='center', fontsize=8)
    
    plt.tight_layout()
    
    # Create title with province information
    title = f'Monthly Capacity Factors & Load Factors - {planning_horizon}'
    if target_province:
        title += f' ({target_province})'
    
    # fig.suptitle(title, fontsize=16, fontweight='bold', y=0.98)
    
    # Save the plot
    fig.savefig(snakemake.output["capacity_factors"], dpi=150, bbox_inches='tight')
    plt.close()
    
    # Save monthly capacity factors to CSV
    save_monthly_data_to_csv(monthly_cf, monthly_max_cf, monthly_load, planning_horizon, target_province)
    
    # Print summary statistics
    province_info = f" - {target_province}" if target_province else ""
    print(f"\nCapacity factor monthly statistics - {planning_horizon}{province_info}")
    print("=" * 50)
    for tech, cf_data in monthly_cf.items():
        if not cf_data.empty:
            avg_cf = cf_data.mean()
            max_cf = cf_data.max()
            min_cf = cf_data.min()
            print(f"{tech:15s}: avg={avg_cf:.3f}, max={max_cf:.3f}, min={min_cf:.3f}")
    
    print(f"\nMonthly max capacity factor statistics - {planning_horizon}{province_info}")
    print("=" * 50)
    for tech, max_cf_data in monthly_max_cf.items():
        if not max_cf_data.empty:
            avg_max_cf = max_cf_data.mean()
            max_max_cf = max_cf_data.max()
            min_max_cf = max_cf_data.min()
            print(f"{tech:15s}: avg={avg_max_cf:.3f}, max={max_max_cf:.3f}, min={min_max_cf:.3f}")
    
    print(f"\nLoad factor monthly statistics - {planning_horizon}{province_info}")
    print("=" * 50)
    for load_type, load_data in monthly_load.items():
        if not load_data.empty:
            avg_load = load_data.mean()
            max_load = load_data.max()
            min_load = load_data.min()
            print(f"{load_type:15s}: avg={avg_load:.3f}, max={max_load:.3f}, min={min_load:.3f}")



def _default_two_scenario_network_paths(planning_horizon="2050"):
    """
    Resolve default network paths for MMMU (15p) and MMMU_non_flexible from config and results/.
    Returns (path_mmmu, path_non_flexible) or (None, None) if not found.
    """
    import os
    import yaml
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    config_path = os.path.join(base_dir, "config.yaml")
    version = "0120.1H.1"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            if cfg and isinstance(cfg.get("version"), str):
                version = cfg["version"].strip()
        except Exception:
            pass
    results_dir = os.path.join(base_dir, "results")
    for tag in ("FCG", "Neighbor"):
        nc_name = f"postnetwork-ll-current+{tag}-linear2050-{planning_horizon}.nc"
        path_15p = os.path.join(results_dir, f"version-{version}-MMMU-{planning_horizon}-15p", "postnetworks", "positive", nc_name)
        path_nf = os.path.join(results_dir, f"version-{version}-MMMU-{planning_horizon}-non_flexible", "postnetworks", "positive", nc_name)
        if os.path.exists(path_15p) and os.path.exists(path_nf):
            return path_15p, path_nf
    return None, None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Write monthly capacity factor CSVs for MMMU and MMMU_non_flexible. Run from repo root."
    )
    parser.add_argument("--network-mmmu", help="Path to MMMU (15p) scenario network .nc file")
    parser.add_argument("--network-mmmu-non-flexible", help="Path to MMMU_non_flexible scenario network .nc file")
    parser.add_argument("--planning-horizon", default="2050", help="Planning horizon for filenames")
    parser.add_argument("--province", default=None, help="Optional province filter")
    args = parser.parse_args()

    path_mmmu = args.network_mmmu
    path_nf = args.network_mmmu_non_flexible
    if not path_mmmu or not path_nf:
        path_mmmu, path_nf = _default_two_scenario_network_paths(args.planning_horizon)
        if path_mmmu and path_nf:
            print("Using default scenario paths (from config.yaml version and results/):")
            print(f"  MMMU:              {path_mmmu}")
            print(f"  MMMU_non_flexible: {path_nf}")

    # Direct run: two-scenario mode (with explicit or default paths)
    if path_mmmu and path_nf:
        set_plot_style()
        run_two_scenarios_to_csv(
            path_mmmu,
            path_nf,
            planning_horizon=args.planning_horizon,
            target_province=args.province,
        )
        print("Done. Two CSVs written to results/monthly_capacity_factors/ (_15p.csv and _non_flexible.csv).")
        exit(0)

    # When run by Snakemake, snakemake is injected into globals()
    snakemake = globals().get("snakemake")
    if snakemake is not None:
        configure_logging(snakemake)
        set_plot_style()
        config = snakemake.config
        n = pypsa.Network(snakemake.input.network)
        target_province = None
        if hasattr(snakemake.config, "single_node_province") and snakemake.config.get("using_single_node", False):
            target_province = snakemake.config["single_node_province"]
            print(f"Single-node mode detected; filtering results for province {target_province}")
        plot_capacity_factors(n, config, target_province)
        exit(0)

    # No paths and no snakemake: print usage
    print("No default scenario networks found. Pass paths explicitly (run from repo root):")
    print("  python scripts/plot_capacity_factors.py --network-mmmu <path_15p.nc> --network-mmmu-non-flexible <path_non_flexible.nc>")
    print("Default paths are built from config.yaml 'version' and results/version-<version>-MMMU-2050-15p and ...-non_flexible.")
    exit(1) 