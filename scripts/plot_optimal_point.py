#!/usr/bin/env python3
# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
# -*- coding: utf-8 -*-
"""
Plot scatter chart of optimal points showing capacity and net value.

X-axis: Aluminum smelting capacity (10,000 tons/year)
Y-axis: Net value (billion CNY)
Different years represented by different colors.
Requires the config files for the capacity tests.

Outputs (all under results/optimal_points_analysis/ unless overridden):
  - optimal_points_distribution.png   (--plot-type distribution, default)
  - optimal_points_boxplot.png        (--plot-type boxplot)
  - optimal_points_scatter.png        (--plot-type scatter)
  - optimal_points_{type}_data_{timestamp}.csv   (unless --no-csv)
  - optimal_points_{type}_data_latest.csv
  - optimal_points_cache_{hash}.csv   (cache of computed optimal points)
  - optimal_points_metadata_{hash}.json
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path
import logging
import yaml
import argparse
import copy
import glob
import re
from scipy import stats
from scipy.stats import norm
import hashlib
import json

# Set Chinese font
plt.rcParams['font.sans-serif'] = ['Helvetica', 'Arial', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False

# Set logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Aluminum cost aggregation method (keep consistent with plot_capacity_MMM_2050.py)
# Configure by employment scenario (currently only U is used here) and cost type.
ALUMINUM_COST_METHODS = {
    "U": {  # MMMU
        "capital": 0.203355,
        "marginal": 1.4227,
        "standby": 0.0,
        "other": 1.0,
    },
    "F": {  # MMMF (kept for completeness; not used in this script yet)
        "capital": 0.325368,
        "marginal": 1.4227,
        "standby": 0.94847,
        "other": 1.0,
    },
}

def load_config(config_path):
    """
    Load configuration file
    
    Parameters:
    -----------
    config_path : str or Path
        Configuration file path
        
    Returns:
    --------
    dict
        Configuration content
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config
    except Exception as e:
        logger.error(f"Error loading configuration file {config_path}: {str(e)}")
        raise

def generate_cache_key(base_version, capacity_ratios, results_dir):
    """
    Generate cache key based on parameters
    
    Parameters:
    -----------
    base_version : str
        Base version number
    capacity_ratios : list
        List of capacity ratios
    results_dir : str
        Results directory
        
    Returns:
    --------
    str
        Cache key
    """
    # Create a string representation of all parameters
    params_str = f"{base_version}_{'-'.join(capacity_ratios)}_{results_dir}"
    # Generate hash
    return hashlib.md5(params_str.encode()).hexdigest()

def save_optimal_points_cache(optimal_points, cache_key, cache_dir='results/optimal_points_analysis'):
    """
    Save optimal points data to CSV cache
    
    Parameters:
    -----------
    optimal_points : list
        List of optimal points data
    cache_key : str
        Cache key
    cache_dir : str
        Cache directory
    """
    try:
        cache_path = Path(cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)
        
        # Convert to DataFrame
        df = pd.DataFrame(optimal_points)
        
        # Save data
        csv_file = cache_path / f"optimal_points_cache_{cache_key}.csv"
        df.to_csv(csv_file, index=False)
        
        # Save metadata
        metadata = {
            'cache_key': cache_key,
            'timestamp': pd.Timestamp.now().isoformat(),
            'num_points': len(optimal_points),
            'years': sorted(list(set([point['year'] for point in optimal_points]))),
            'markets': sorted(list(set([point['market'] for point in optimal_points]))),
            'flexibilities': sorted(list(set([point['flexibility'] for point in optimal_points])))
        }
        
        metadata_file = cache_path / f"optimal_points_metadata_{cache_key}.json"
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Optimal points data cached to: {csv_file}")
        logger.info(f"Metadata cached to: {metadata_file}")
        
    except Exception as e:
        logger.error(f"Error saving cache: {str(e)}")

def load_optimal_points_cache(cache_key, cache_dir='results/optimal_points_analysis'):
    """
    Load optimal points data from CSV cache
    
    Parameters:
    -----------
    cache_key : str
        Cache key
    cache_dir : str
        Cache directory
        
    Returns:
    --------
    list or None
        List of optimal points data if cache exists and is valid, None otherwise
    """
    try:
        cache_path = Path(cache_dir)
        csv_file = cache_path / f"optimal_points_cache_{cache_key}.csv"
        metadata_file = cache_path / f"optimal_points_metadata_{cache_key}.json"
        
        # Check if both files exist
        if not csv_file.exists() or not metadata_file.exists():
            logger.info("Cache files not found")
            return None
        
        # Load metadata
        with open(metadata_file, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        
        # Check if cache is recent (within 24 hours)
        cache_time = pd.Timestamp(metadata['timestamp'])
        if pd.Timestamp.now() - cache_time > pd.Timedelta(hours=24):
            logger.info("Cache is outdated (older than 24 hours)")
            return None
        
        # Load data
        df = pd.read_csv(csv_file)
        optimal_points = df.to_dict('records')
        
        # Convert year back to int
        for point in optimal_points:
            point['year'] = int(point['year'])
        
        logger.info(f"Loaded optimal points from cache: {len(optimal_points)} points")
        logger.info(f"Cache contains years: {metadata['years']}")
        logger.info(f"Cache contains markets: {metadata['markets']}")
        logger.info(f"Cache contains flexibilities: {metadata['flexibilities']}")
        
        return optimal_points
        
    except Exception as e:
        logger.error(f"Error loading cache: {str(e)}")
        return None

def save_optimal_points_to_csv(optimal_points, plot_type, output_dir='results/optimal_points_analysis'):
    """
    Save optimal points data to CSV file for easy migration
    
    Parameters:
    -----------
    optimal_points : list
        List of optimal points data
    plot_type : str
        Type of plot (for filename)
    output_dir : str
        Output directory
    """
    try:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Convert to DataFrame
        df = pd.DataFrame(optimal_points)
        
        # Add additional calculated columns (national demand, 10k t/y; same scenario as
        # calculate_actual_capacity_ratio / generate_test_configs._calculate_actual_capacity_ratio)
        demand_by_year = {
            2030: 2902.417177819193,
            2040: 1508.1703393209764,
            2050: 1166.6836345743664,
        }
        
        # Calculate excess ratio for each point
        excess_ratios = []
        for point in optimal_points:
            capacity = point['capacity'] * 100  # Convert to 10,000 tons/year
            demand = demand_by_year.get(point['year'], 0)
            excess_ratio = calculate_excess_ratio(capacity, demand)
            excess_ratios.append(excess_ratio)
        
        df['excess_ratio'] = excess_ratios
        df['demand_10k_tons'] = df['year'].map(demand_by_year)
        df['capacity_10k_tons'] = df['capacity'] * 100
        
        # Reorder columns for better readability
        column_order = ['year', 'market', 'flexibility', 'capacity', 'capacity_10k_tons', 
                       'net_value', 'excess_ratio', 'demand_10k_tons']
        df = df[column_order]
        
        # Save to CSV
        timestamp = pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')
        csv_file = output_path / f"{plot_type}_data_{timestamp}.csv"
        df.to_csv(csv_file, index=False, encoding='utf-8')
        
        # Also save a version without timestamp for easy access
        csv_file_latest = output_path / f"{plot_type}_data_latest.csv"
        df.to_csv(csv_file_latest, index=False, encoding='utf-8')
        
        logger.info(f"Optimal points data saved to: {csv_file}")
        logger.info(f"Latest data also saved to: {csv_file_latest}")
        
        # Print summary statistics
        logger.info(f"Data summary:")
        logger.info(f"  Total points: {len(df)}")
        logger.info(f"  Years: {sorted(df['year'].unique())}")
        logger.info(f"  Markets: {sorted(df['market'].unique())}")
        logger.info(f"  Flexibilities: {sorted(df['flexibility'].unique())}")
        logger.info(f"  Capacity range: {df['capacity'].min():.1f} - {df['capacity'].max():.1f} (10k tons/year)")
        logger.info(f"  Net value range: {df['net_value'].min():.2f} - {df['net_value'].max():.2f} (Billion CNY)")
        
    except Exception as e:
        logger.error(f"Error saving CSV: {str(e)}")

def find_available_years(results_dir, base_version):
    """
    Find available year data
    
    Parameters:
    -----------
    results_dir : str
        Results directory
    base_version : str
        Base version number
        
    Returns:
    --------
    list
        List of available years
    """
    available_years = []
    results_path = Path(results_dir)
    
    # Find all possible year directories
    for year in [2030, 2040, 2050]:
        # Find version directories containing this year
        year_pattern = f"version-{base_version}-*{year}*"
        version_dirs = list(results_path.glob(year_pattern))
        
        for version_dir in version_dirs:
            # Check if data exists for this year
            summary_dir = version_dir / 'summary' / 'postnetworks' / 'positive'
            if summary_dir.exists():
                for tag in ("FCG", "Neighbor"):
                    year_dir = summary_dir / f"postnetwork-ll-current+{tag}-linear2050-{year}"
                    if year_dir.exists() and (year_dir / 'costs.csv').exists():
                        available_years.append(year)
                        break
            if year in available_years:
                break
    
    if not available_years:
        raise FileNotFoundError(
            f"No year data found under {results_path} for base_version={base_version}. "
            "Ensure postnetwork cost results exist (FCG or Neighbor) for at least one of 2030, 2040, 2050."
        )
    
    return sorted(list(set(available_years)))

def load_costs_data(version_name, year, results_dir='results'):
    """
    Load cost data for specified version
    
    Parameters:
    -----------
    version_name : str
        Version name, e.g. '0814.4H.2-MMM-2050-100p'
    year : int
        Year
    results_dir : str
        Results directory
        
    Returns:
    --------
    pd.DataFrame
        Cost data. Raises FileNotFoundError if no costs file exists; propagates other errors.
    """
    try:
        # Version aliases:
        # - Some scenarios reuse the same non-flexible results (see scripts/generate_test_configs.py):
        #   non-flexible uses mid flex + mid demand + unfavorable employment regardless of requested flexibility.
        #   Example: ...-LMLU-2030-non_flexible should reuse ...-MMLU-2030-non_flexible.
        version_aliases = [str(version_name)]
        if "non_flexible" in str(version_name):
            v = str(version_name)
            v2 = re.sub(r"-(?:L|H|N)M([LMH])U-(\d+)-non_flexible$", r"-MM\1U-\2-non_flexible", v)
            if v2 != v:
                version_aliases.append(v2)

        # Resolve version directory (some workflows may already include the 'version-' prefix)
        version_dir = None
        for v in version_aliases:
            cand = Path(results_dir) / f"version-{v}"
            if cand.exists():
                version_dir = cand
                break
            alt = Path(results_dir) / v
            if alt.exists():
                version_dir = alt
                break
        if version_dir is None:
            version_dir = Path(results_dir) / f"version-{version_aliases[0]}"

        # Build file path (prefer FCG, then Neighbor)
        candidates = [
            version_dir / f"summary/postnetworks/positive/postnetwork-ll-current+FCG-linear2050-{year}/costs.csv",
            version_dir / f"summary/postnetworks/positive/postnetwork-ll-current+Neighbor-linear2050-{year}/costs.csv",
        ]

        file_path = next((p for p in candidates if p.exists()), None)

        # Fallback: search more flexibly for costs.csv in case directory naming differs
        if file_path is None:
            patterns = [
                f"summary/postnetworks/**/postnetwork-ll-current+FCG*{year}*/costs.csv",
                f"summary/postnetworks/**/postnetwork-ll-current+Neighbor*{year}*/costs.csv",
                f"summary/postnetworks/**/postnetwork*FCG*{year}*/costs.csv",
                f"summary/postnetworks/**/postnetwork*Neighbor*{year}*/costs.csv",
            ]
            for pat in patterns:
                matches = sorted(version_dir.glob(pat))
                if matches:
                    file_path = matches[0]
                    break

        if file_path is None or not file_path.exists():
            tried = " and ".join(str(p) for p in candidates)
            raise FileNotFoundError(
                "Costs file not found. "
                f"Tried: {tried}. "
                f"Also searched under: {version_dir}/summary/postnetworks/** for year={year}. "
                f"Version aliases tried: {version_aliases}"
            )
        
        # Read CSV file
        df = pd.read_csv(file_path, header=None)
        
        # Handle multi-level index structure
        if len(df.columns) >= 4:
            # Set multi-level index: first two columns as index, third column as technology name
            df.set_index([0, 1, 2], inplace=True)
            # Rename last column as numeric column name
            df.columns = [df.columns[0]]
            # Convert numeric column to numeric type
            df[df.columns[0]] = pd.to_numeric(df[df.columns[0]], errors='coerce')
        else:
            # If insufficient columns, use default multi-level index
            df = pd.read_csv(file_path, index_col=[0, 1])
            numeric_col = df.columns[0]
            df[numeric_col] = pd.to_numeric(df[numeric_col], errors='coerce')
        
        return df
        
    except Exception as e:
        logger.error(f"Error loading data: {str(e)}")
        raise

def calculate_cost_categories(costs_data):
    """
    Calculate cost categories
    
    Parameters:
    -----------
    costs_data : pd.DataFrame
        Cost data
        
    Returns:
    --------
    dict
        Cost data organized by category
    """
    if costs_data is None or costs_data.empty:
        return {}
    
    # Define cost type and resource combination category mapping
    cost_category_mapping = {
        # variable cost-non-renewable - Non-renewable energy variable costs
        ('marginal', 'coal'): 'variable cost-non-renewable',
        ('marginal', 'coal power plant'): 'variable cost-non-renewable',
        ('marginal', 'coal cc'): 'variable cost-non-renewable',
        ('marginal', 'gas'): 'variable cost-non-renewable',
        ('marginal', 'nuclear'): 'variable cost-non-renewable',
        ('marginal', 'CHP coal'): 'variable cost-non-renewable',
        ('marginal', 'CHP gas'): 'variable cost-non-renewable',
        ('marginal', 'OCGT gas'): 'variable cost-non-renewable',
        ('marginal', 'coal boiler'): 'variable cost-non-renewable',
        ('marginal', 'gas boiler'): 'variable cost-non-renewable',
        
        # capital-non-renewable - Non-renewable energy capital costs
        ('capital', 'coal'): 'capital-non-renewable',
        ('capital', 'coal power plant'): 'capital-non-renewable',
        ('capital', 'coal cc'): 'capital-non-renewable',
        ('capital', 'gas'): 'capital-non-renewable',
        ('capital', 'nuclear'): 'capital-non-renewable',
        ('capital', 'CHP coal'): 'capital-non-renewable',
        ('capital', 'CHP gas'): 'capital-non-renewable',
        ('capital', 'OCGT gas'): 'capital-non-renewable',
        ('capital', 'coal boiler'): 'capital-non-renewable',
        ('capital', 'gas boiler'): 'capital-non-renewable',
        
        # capital-demand side - Demand-side capital costs
        ('capital', 'heat pump'): 'heating-electrification',
        ('capital', 'resistive heater'): 'heating-electrification',
        
        # capital-renewable - Renewable energy capital costs
        ('capital', 'hydro_inflow'): 'capital-renewable',
        ('capital', 'hydroelectricity'): 'capital-renewable',
        ('capital', 'offwind'): 'capital-renewable',
        ('capital', 'onwind'): 'capital-renewable',
        ('capital', 'solar'): 'capital-renewable',
        ('capital', 'solar thermal'): 'capital-renewable',
        ('capital', 'biomass'): 'capital-renewable',
        ('capital', 'biogas'): 'capital-renewable',
        
        # transmission lines - Transmission lines
        ('capital', 'AC'): 'transmission lines',
        ('capital', 'stations'): 'transmission lines',
        
        # batteries - Battery storage
        ('capital', 'battery'): 'batteries',
        ('capital', 'battery discharger'): 'batteries',
        ('marginal', 'battery'): 'batteries',
        ('marginal', 'battery discharger'): 'batteries',
        
        # long-duration storages - Long-duration storage
        ('capital', 'PHS'): 'long-duration storages',
        ('capital', 'water tanks'): 'long-duration storages',
        ('capital', 'H2'): 'long-duration storages',
        ('capital', 'H2 CHP'): 'long-duration storages',
        ('marginal', 'PHS'): 'long-duration storages',
        ('marginal', 'water tanks'): 'long-duration storages',
        ('marginal', 'H2'): 'long-duration storages',
        ('marginal', 'H2 CHP'): 'long-duration storages',
        
        # Other categories
        ('capital', 'CO2 capture'): 'capital-non-renewable',
        ('marginal', 'CO2 capture'): 'variable cost-non-renewable',
        ('capital', 'Sabatier'): 'capital-non-renewable',
        ('marginal', 'Sabatier'): 'variable cost-non-renewable',
        ('capital', 'CO2'): 'capital-non-renewable',
        ('marginal', 'CO2'): 'variable cost-non-renewable',
        ('capital', 'DAC'): 'capital-non-renewable',
        ('marginal', 'DAC'): 'variable cost-non-renewable',
    }
    
    # Organize data by cost category
    category_costs = {}
    
    for idx in costs_data.index:
        if len(idx) >= 3:
            component_type, cost_type, carrier = idx[0], idx[1], idx[2]
            
            # Use category mapping
            category_key = (cost_type, carrier)
            category_name = cost_category_mapping.get(category_key, f"{cost_type} - {carrier}")
            
            if category_name not in category_costs:
                category_costs[category_name] = 0
            
            value = costs_data.loc[idx].iloc[0]
            if not pd.isna(value):
                category_costs[category_name] += value
    
    return category_costs

def calculate_total_emissions_from_costs(costs_data):
    """
    Calculate total carbon emissions from cost data (estimated through coal and gas marginal costs)
    
    Parameters:
    -----------
    costs_data : pd.DataFrame
        Cost data
        
    Returns:
    --------
    float
        Total carbon emissions (tons CO2)
    """
    if costs_data is None or costs_data.empty:
        return 0
    
    total_emissions = 0
    
    for idx in costs_data.index:
        if len(idx) >= 3:
            component_type, cost_type, carrier = idx[0], idx[1], idx[2]
            if isinstance(carrier, str) and 'coal' in carrier.lower() and cost_type == 'marginal':
                value = costs_data.loc[idx].iloc[0]
                if pd.notna(value):
                    total_emissions += value
            elif isinstance(carrier, str) and 'gas' in carrier.lower() and cost_type == 'marginal':
                value = costs_data.loc[idx].iloc[0]
                if pd.notna(value):
                    total_emissions += value
    
    return total_emissions



def calculate_actual_capacity_ratio(year: int, cap_ratio: float, demand_level: str) -> float:
    """
    Calculate actual capacity ratio
    
    Args:
        year: Year
        cap_ratio: Excess capacity retention ratio (e.g., 0.1 means 10%)
        demand_level: Demand level ('mid')
        
    Returns:
        Actual capacity ratio
    """
    # National installed capacity (10k t/y) and demand (10k t/y) for mid-demand scenario;
    # keep in sync with generate_test_configs._calculate_actual_capacity_ratio.
    total_capacity = 4500
    demand_by_year = {
        "2030": 2902.417177819193,
        "2040": 1508.1703393209764,
        "2050": 1166.6836345743664,
    }
    
    demand = demand_by_year.get(str(year), 0)
    
    # Calculate actual capacity ratio: demand/capacity × (1-cap) + cap
    actual_ratio = (demand / total_capacity) * (1 - cap_ratio) + cap_ratio
    
    return actual_ratio

def find_optimal_points(base_version, capacity_ratios, results_dir='results', use_cache=True):
    """
    Find optimal points for each year-market-flexibility combination
    
    Parameters:
    -----------
    base_version : str
        Base version number
    capacity_ratios : list
        List of capacity ratios
    results_dir : str
        Results directory
    use_cache : bool
        Whether to use cache if available
        
    Returns:
    --------
    list
        List containing optimal point information, each element is (year, market, flexibility, capacity, net_value)
    """
    # Generate cache key
    cache_key = generate_cache_key(base_version, capacity_ratios, results_dir)
    
    # Try to load from cache first
    if use_cache:
        cached_data = load_optimal_points_cache(cache_key)
        if cached_data is not None:
            logger.info("Using cached optimal points data")
            return cached_data
        else:
            logger.info("Cache not available or outdated, computing optimal points...")
    
    # Euro to CNY conversion rate (keep consistent with plot_capacity_MMM_2050.py)
    EUR_TO_CNY = 7.868
    
    # Find available years
    available_years = find_available_years(results_dir, base_version)
    logger.info(f"Found available years: {available_years}")
    
    # Define market opportunities and flexibility levels
    markets = ['L', 'M', 'H']
    flexibilities = ['L', 'M', 'H', 'N']  # low, mid, high, non_constrained
    
    optimal_points = []
    
    for year in available_years:
        for market in markets:
            for flexibility in flexibilities:
                logger.info(f"Analyzing optimal point for {year}-{market}-{flexibility}...")
                
                # Build version names - use different base_version for different scenarios
                # Extract the base part and construct scenario-specific base_version
                base_parts = base_version.split('-')
                if len(base_parts) >= 2:
                    # Use the scenario part (e.g., MMM, HML, etc.) from base_version
                    scenario_part = base_parts[1]  # e.g., "MMM", "HML", etc.
                    scenario_base_version = f"{base_parts[0]}-{scenario_part}"
                else:
                    # Fallback to original base_version
                    scenario_base_version = base_version
                
                version_names = []
                config_versions = {}
                
                employment_letter = 'U'

                for ratio in capacity_ratios:
                    # Version format: scenario_base_version-{flexibility}{demand}{market}-{year}-{ratio}
                    # Demand is fixed as 'M' (mid)
                    version = f"{scenario_base_version}-{flexibility}M{market}{employment_letter}-{year}-{ratio}"
                    version_names.append(version)
                    config_versions[ratio] = version
                
                # Baseline versions
                aluminum_baseline_version = f"{scenario_base_version}-{flexibility}M{market}{employment_letter}-{year}-5p"
                # non-flexible baseline reuses a single fixed scenario (mid flex + mid demand + unfavorable)
                power_baseline_version = f"{scenario_base_version}-MM{market}U-{year}-non_flexible"
                
                # Collect data
                costs_data = {}
                baseline_data = {}
                
                # Load baseline data (raises if file missing)
                aluminum_baseline = load_costs_data(aluminum_baseline_version, year, results_dir)
                baseline_data['aluminum'] = aluminum_baseline
                
                power_baseline = load_costs_data(power_baseline_version, year, results_dir)
                baseline_data['power'] = power_baseline
                
                # Load data for each capacity ratio (raises if any file missing)
                for ratio in capacity_ratios:
                    version_name = config_versions[ratio]
                    costs = load_costs_data(version_name, year, results_dir)
                    costs_data[ratio] = costs
                
                # All required data loaded (otherwise load_costs_data already raised)
                
                # Calculate net values (same method as plot_capacity_multi_year_market_comparison)
                power_cost_changes = []
                aluminum_cost_changes = []
                capacity_values = []
                
                for ratio in capacity_ratios:
                    if ratio in costs_data and 'power' in baseline_data:
                        # Calculate power system cost changes (cost reduction is positive)
                        current_costs = calculate_cost_categories(costs_data[ratio])
                        baseline_costs = calculate_cost_categories(baseline_data['power'])
                        
                        # Calculate total cost change (excluding aluminum related)
                        power_cost_change = 0
                        for category, value in current_costs.items():
                            if 'aluminum' not in category.lower():
                                baseline_value = baseline_costs.get(category, 0)
                                power_cost_change += (value - baseline_value)
                        
                        # Cost reduction is positive direction, so take negative value
                        power_cost_changes.append(-power_cost_change * EUR_TO_CNY)
                    else:
                        power_cost_changes.append(0)
                    
                    if ratio in costs_data and 'aluminum' in baseline_data:
                        # Calculate aluminum cost changes (cost reduction is positive),
                        # using the same weighting method as plot_capacity_MMM_2050.py.
                        current_costs = calculate_cost_categories(costs_data[ratio])
                        baseline_costs = calculate_cost_categories(baseline_data['aluminum'])

                        method = ALUMINUM_COST_METHODS.get(employment_letter, ALUMINUM_COST_METHODS.get("U", {}))
                        aluminum_change = 0
                        aluminum_startup_change = 0
                        aluminum_shutdown_change = 0
                        has_startup = False
                        has_shutdown = False

                        for category, value in current_costs.items():
                            name = category.lower()
                            if 'aluminum' not in name:
                                continue

                            # Determine cost type from category prefix
                            if name.startswith('capital'):
                                weight = method.get("capital", 1.0)
                            elif name.startswith('marginal'):
                                weight = method.get("marginal", 1.0)
                            elif name.startswith('standby'):
                                weight = method.get("standby", 1.0)
                            else:
                                weight = method.get("other", 1.0)

                            if weight == 0:
                                continue

                            baseline_value = baseline_costs.get(category, 0)
                            delta = weight * (value - baseline_value)

                            # startup/shutdown may contain noisy stats: when both exist,
                            # we only keep the smaller |delta| (with sign) and double it.
                            if "startup" in name:
                                aluminum_startup_change += delta
                                has_startup = True
                            elif "shutdown" in name:
                                aluminum_shutdown_change += delta
                                has_shutdown = True
                            else:
                                aluminum_change += delta

                        # Merge startup/shutdown contributions
                        if has_startup and has_shutdown:
                            chosen = (
                                aluminum_startup_change
                                if abs(aluminum_startup_change) <= abs(aluminum_shutdown_change)
                                else aluminum_shutdown_change
                            )
                            aluminum_change += 2 * chosen
                        elif has_startup:
                            aluminum_change += aluminum_startup_change
                        elif has_shutdown:
                            aluminum_change += aluminum_shutdown_change

                        # Cost reduction is positive direction, so take negative value
                        aluminum_cost_changes.append(-aluminum_change * EUR_TO_CNY)
                    else:
                        aluminum_cost_changes.append(0)
                    
                    # Read capacity ratio values and calculate actual capacity
                    # Config name must match scenario code including employment (e.g. LMLU)
                    scenario_code_for_config = f"{flexibility}M{market}{employment_letter}"
                    config_file = f"configs/config_{scenario_code_for_config}_{year}_{ratio}.yaml"
                    config = load_config(config_file)
                    if 'aluminum' in config and 'capacity_ratio' in config['aluminum']:
                        capacity_ratio = config['aluminum']['capacity_ratio']
                    elif 'aluminum_capacity_ratio' in config:
                        capacity_ratio = config['aluminum_capacity_ratio']
                    else:
                        raise KeyError(
                            f"Config {config_file} must contain 'aluminum_capacity_ratio' or 'aluminum.capacity_ratio'."
                        )
                    cap_ratio_decimal = float(ratio.replace('p', '')) / 100.0
                    actual_capacity_ratio = calculate_actual_capacity_ratio(year, cap_ratio_decimal, 'mid')
                    actual_capacity = 45 * actual_capacity_ratio
                    capacity_values.append(actual_capacity)
                
                # Calculate net cost savings (same as plot_capacity_multi_year_market_comparison)
                net_cost_savings = [power_cost_changes[i] + aluminum_cost_changes[i] for i in range(len(capacity_values))]
                
                # Find the point with maximum net savings
                if net_cost_savings:
                    max_saving_index = np.argmax(net_cost_savings)
                    optimal_capacity = capacity_values[max_saving_index]
                    optimal_net_value = net_cost_savings[max_saving_index]
                    
                    optimal_points.append({
                        'year': year,
                        'market': market,
                        'flexibility': flexibility,
                        'capacity': optimal_capacity,
                        'net_value': optimal_net_value / 1e9  # Convert to billion CNY
                    })
                    
                    logger.info(f"{year}-{market}-{flexibility} optimal point: capacity={optimal_capacity:.1f} 10,000 tons/year, net_value={optimal_net_value/1e9:.2f}B CNY")
    
    # Save to cache
    if optimal_points:
        save_optimal_points_cache(optimal_points, cache_key)
    
    return optimal_points

def calculate_excess_ratio(capacity, demand):
    """
    Calculate excess ratio: 1 - (demand/capacity)
    
    Parameters:
    -----------
    capacity : float
        Actual capacity (10,000 tons/year)
    demand : float
        Demand (10,000 tons/year)
        
    Returns:
    --------
    float
        Excess ratio
    """
    excess_ratio = 1 - (demand / capacity) if capacity > 0 else 0
    return excess_ratio

def plot_optimal_points_distribution(use_cache=True, save_csv=True):
    """
    Plot optimal points distribution
    Top: Point distribution and density function of optimal excess ratio
    Bottom: Distribution and probability density function of optimal net value
    """
    from scipy import stats
    from scipy.stats import norm
    
    # Load base version from main config file (raises if missing)
    main_config = load_config('config.yaml')
    if 'version' not in main_config:
        raise KeyError("config.yaml must contain 'version'.")
    base_version = main_config['version']
    logger.info(f"Loaded base version from main config: {base_version}")
    
    # Define capacity ratios
    capacity_ratios = ['5p', '10p', '20p', '30p', '40p', '50p', '60p', '70p', '80p', '90p', '100p']
    
    # Find all optimal points
    optimal_points = find_optimal_points(base_version, capacity_ratios, 'results', use_cache)
    
    if not optimal_points:
        logger.error("No optimal point data found")
        return
    
    # Save data to CSV if requested
    if save_csv:
        save_optimal_points_to_csv(optimal_points, 'optimal_points_distribution')
    
    # Group data by year
    years = sorted(list(set([point['year'] for point in optimal_points])))
    
    # National demand (10k t/y); same series as calculate_actual_capacity_ratio
    demand_by_year = {
        2030: 2902.417177819193,
        2040: 1508.1703393209764,
        2050: 1166.6836345743664,
    }
    
    # Calculate excess ratio and net value for each year
    year_data = {}
    for year in years:
        year_points = [point for point in optimal_points if point['year'] == year]
        excess_ratios = []
        net_values = []
        
        for point in year_points:
            # Calculate excess ratio: 1 - (demand/retention_ratio)
            capacity = point['capacity'] * 100  # Convert to 10,000 tons/year
            demand = demand_by_year.get(year, 0)
            excess_ratio = calculate_excess_ratio(capacity, demand)
            excess_ratios.append(excess_ratio)
            net_values.append(point['net_value'])
        
        year_data[year] = {
            'excess_ratios': excess_ratios,
            'net_values': net_values
        }
    
    # Create subplots
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 10))
    
    # Color settings
    colors = plt.cm.viridis(np.linspace(0, 1, len(years)))
    year_colors = dict(zip(years, colors))
    
    # Top plot: Optimal excess ratio distribution
    ax1.set_title('Optimal Excess Ratio Distribution', fontsize=16, fontweight='bold', pad=20)
    
    for year in years:
        excess_ratios = year_data[year]['excess_ratios']
        if len(excess_ratios) > 1:  # Need at least 2 points to fit distribution
            # Plot scatter points
            x_positions = np.random.normal(year, 0.1, len(excess_ratios))  # Add small random offset to avoid overlap
            ax1.scatter(x_positions, excess_ratios, 
                       c=year_colors[year], alpha=0.7, s=100, 
                       label=f'{year} (n={len(excess_ratios)})')
            
            # Fit Gaussian distribution
            mu, sigma = norm.fit(excess_ratios)
            
            # Plot probability density function
            x_range = np.linspace(min(excess_ratios) - 2*sigma, max(excess_ratios) + 2*sigma, 100)
            pdf = norm.pdf(x_range, mu, sigma)
            
            # Scale PDF to appropriate range and offset to corresponding year
            pdf_scaled = pdf * 1.0 + year  # Scale and offset (increased from 0.3 to 1.0)
            ax1.plot(pdf_scaled, x_range, color=year_colors[year], linewidth=3, alpha=0.9)
            
            # Add mean and standard deviation information
            ax1.text(year, max(excess_ratios) + 0.1, f'Mean={mu:.3f}', 
                    ha='center', va='bottom', fontsize=10, 
                    bbox=dict(boxstyle="round,pad=0.3", facecolor=year_colors[year], alpha=0.3))
    
    ax1.set_xlabel('Year', fontsize=14, fontweight='bold')
    ax1.set_ylabel('Excess Ratio', fontsize=14, fontweight='bold')
    ax1.set_xticks(years)
    ax1.grid(True, alpha=0.3)
    # ax1.legend(loc='upper right', fontsize=12)  # Removed legend
    
    # Bottom plot: Optimal net value distribution
    ax2.set_title('Optimal Net Benefit Distribution', fontsize=16, fontweight='bold', pad=20)
    
    for year in years:
        net_values = year_data[year]['net_values']
        if len(net_values) > 1:  # Need at least 2 points to fit distribution
            # Plot scatter points
            x_positions = np.random.normal(year, 0.1, len(net_values))  # Add small random offset to avoid overlap
            ax2.scatter(x_positions, net_values, 
                       c=year_colors[year], alpha=0.7, s=100, 
                       label=f'{year} (n={len(net_values)})')
            
            # Fit Gaussian distribution
            mu, sigma = norm.fit(net_values)
            
            # Plot probability density function
            x_range = np.linspace(min(net_values) - 2*sigma, max(net_values) + 2*sigma, 100)
            pdf = norm.pdf(x_range, mu, sigma)
            
            # Scale PDF to appropriate range and offset to corresponding year
            pdf_scaled = pdf * 1.0 + year  # Scale and offset (increased from 0.3 to 1.0)
            ax2.plot(pdf_scaled, x_range, color=year_colors[year], linewidth=3, alpha=0.9)
            
            # Add mean and standard deviation information
            ax2.text(year, max(net_values) + 0.5, f'Mean={mu:.2f}', 
                    ha='center', va='bottom', fontsize=10, 
                    bbox=dict(boxstyle="round,pad=0.3", facecolor=year_colors[year], alpha=0.3))
    
    ax2.set_xlabel('Year', fontsize=14, fontweight='bold')
    ax2.set_ylabel('Net Benefit (Billion CNY)', fontsize=14, fontweight='bold')
    ax2.set_xticks(years)
    ax2.grid(True, alpha=0.3)
    # ax2.legend(loc='upper right', fontsize=12)  # Removed legend
    
    plt.tight_layout()
    
    # Save plot
    output_dir = Path('results/optimal_points_analysis')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    plot_file = output_dir / "optimal_points_distribution.png"
    plt.savefig(plot_file, dpi=300, bbox_inches='tight')
    logger.info(f"Optimal points distribution plot saved to: {plot_file}")
    
    # Show plot
    # plt.show()

def plot_optimal_points_boxplot(use_cache=True, save_csv=True):
    """
    Plot box plot of optimal points showing capacity and net value distribution by year
    """
    # Load base version from main config file (raises if missing)
    main_config = load_config('config.yaml')
    if 'version' not in main_config:
        raise KeyError("config.yaml must contain 'version'.")
    base_version = main_config['version']
    logger.info(f"Loaded base version from main config: {base_version}")
    
    # Define capacity ratios
    capacity_ratios = ['5p', '10p', '20p', '30p', '40p', '50p', '60p', '70p', '80p', '90p', '100p']
    
    # Find all optimal points
    optimal_points = find_optimal_points(base_version, capacity_ratios, 'results', use_cache)
    
    if not optimal_points:
        logger.error("No optimal point data found")
        return
    
    # Save data to CSV if requested
    if save_csv:
        save_optimal_points_to_csv(optimal_points, 'optimal_points_boxplot')
    
    # Create box plot
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 8))
    
    # Group data by year
    years = sorted(list(set([point['year'] for point in optimal_points])))
    colors = plt.cm.viridis(np.linspace(0, 1, len(years)))
    year_colors = dict(zip(years, colors))
    
    # Prepare data for box plots
    capacity_data = []
    net_value_data = []
    year_labels = []
    
    for year in years:
        year_points = [point for point in optimal_points if point['year'] == year]
        capacities = [point['capacity'] for point in year_points]
        net_values = [point['net_value'] for point in year_points]
        
        capacity_data.append(capacities)
        net_value_data.append(net_values)
        year_labels.append(f'{year}\n(n={len(capacities)})')
    
    # Left plot: Capacity box plot
    bp1 = ax1.boxplot(capacity_data, labels=year_labels, patch_artist=True, 
                      boxprops=dict(alpha=0.7), medianprops=dict(color='black', linewidth=2),
                      showfliers=False)
    
    # Color the boxes
    for patch, year in zip(bp1['boxes'], years):
        patch.set_facecolor(year_colors[year])
    
    ax1.set_title('Optimal Capacity Distribution by Year', fontsize=16, fontweight='bold', pad=20)
    ax1.set_xlabel('Year', fontsize=14, fontweight='bold')
    ax1.set_ylabel('Aluminum Smelting Capacity (10,000 tons/year)', fontsize=14, fontweight='bold')
    ax1.grid(True, alpha=0.3)
    ax1.tick_params(axis='both', which='major', labelsize=12)
    
    # Format y-axis to show integers only
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{int(x)}'))
    
    # Annual demand overlay (Mt/year); same totals as 10k-t/y series / 100 (see save_optimal_points_to_csv)
    demand_by_year = {
        2030: 29.0241717,
        2040: 15.0817033,
        2050: 11.6668363,
    }
    
    # Colors for demand lines
    demand_colors = {2030: 'red', 2040: 'orange', 2050: 'purple'}
    
    for i, year in enumerate(years, 1):
        if year in demand_by_year:
            demand = demand_by_year[year]
            ax1.axhline(y=demand, xmin=(i-1)/len(years), xmax=i/len(years), 
                       color=demand_colors[year], linestyle='--', linewidth=3, alpha=0.8)
    
    # Right plot: Net value box plot
    bp2 = ax2.boxplot(net_value_data, labels=year_labels, patch_artist=True, 
                      boxprops=dict(alpha=0.7), medianprops=dict(color='black', linewidth=2),
                      showfliers=False)
    
    # Color the boxes
    for patch, year in zip(bp2['boxes'], years):
        patch.set_facecolor(year_colors[year])
    
    ax2.set_title('Optimal Net Value Distribution by Year', fontsize=16, fontweight='bold', pad=20)
    ax2.set_xlabel('Year', fontsize=14, fontweight='bold')
    ax2.set_ylabel('Net System Benefit (Billion CNY)', fontsize=14, fontweight='bold')
    ax2.grid(True, alpha=0.3)
    ax2.tick_params(axis='both', which='major', labelsize=12)
    
    # Format y-axis to show integers only
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{int(x)}'))
    
    # Add legend for years
    legend_elements = []
    for year in years:
        legend_elements.append(plt.Rectangle((0,0),1,1, facecolor=year_colors[year], alpha=0.7, label=f'{year}'))
    
    # Add demand lines to legend
    for year, demand in demand_by_year.items():
        if year in years:
            legend_elements.append(plt.Line2D([0], [0], color=demand_colors[year], linestyle='--', linewidth=3, 
                                            label=f'{year} Demand: {demand:.0f} Mt/year'))
    
    # Add legend
    ax1.legend(handles=legend_elements, loc='upper right', fontsize=12)
    
    plt.tight_layout()
    
    # Save plot
    output_dir = Path('results/optimal_points_analysis')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    plot_file = output_dir / "optimal_points_boxplot.png"
    plt.savefig(plot_file, dpi=300, bbox_inches='tight')
    logger.info(f"Optimal points box plot saved to: {plot_file}")
    
    # Show plot
    # plt.show()

def plot_optimal_points_scatter(use_cache=True, save_csv=True):
    """
    Plot scatter chart of optimal points showing capacity and net value
    """
    # Load base version from main config file (raises if missing)
    main_config = load_config('config.yaml')
    if 'version' not in main_config:
        raise KeyError("config.yaml must contain 'version'.")
    base_version = main_config['version']
    logger.info(f"Loaded base version from main config: {base_version}")
    
    # Define capacity ratios
    capacity_ratios = ['5p', '10p', '20p', '30p', '40p', '50p', '60p', '70p', '80p', '90p', '100p']
    
    # Find all optimal points
    optimal_points = find_optimal_points(base_version, capacity_ratios, 'results', use_cache)
    
    if not optimal_points:
        logger.error("No optimal point data found")
        return
    
    # Save data to CSV if requested
    if save_csv:
        save_optimal_points_to_csv(optimal_points, 'optimal_points_scatter')
    
    # Create scatter plot
    fig, ax = plt.subplots(figsize=(10, 10))
    
    # Group data by year
    years = sorted(list(set([point['year'] for point in optimal_points])))
    colors = plt.cm.viridis(np.linspace(0, 1, len(years)))
    year_colors = dict(zip(years, colors))
    
    # Create different markers for market opportunities
    markets = ['L', 'M', 'H']
    market_markers = {'L': 'o', 'M': 's', 'H': '^'}
    
    # Create different colors for flexibility levels
    flexibilities = ['L', 'M', 'H', 'N']
    flexibility_colors = {'L': 'blue', 'M': 'green', 'H': 'orange', 'N': 'red'}
    
    # Plot scatter points
    for point in optimal_points:
        year = point['year']
        market = point['market']
        flexibility = point['flexibility']
        capacity = point['capacity']
        net_value = point['net_value']
        
        # Use year color for main color, flexibility for edge color
        ax.scatter(capacity, net_value, 
                  c=year_colors[year], 
                  marker=market_markers[market],
                  s=200, alpha=0.7, 
                  edgecolors=flexibility_colors[flexibility], 
                  linewidth=2)
    
    # Add labels
    ax.set_xlabel('Aluminum Smelting Capacity (10,000 tons/year)', fontsize=15, fontweight='bold')
    ax.set_ylabel('Net System Benefit (Billion CNY)', fontsize=15, fontweight='bold')
    # ax.set_title('Optimal Points Analysis: Capacity vs Net Value', fontsize=16, fontweight='bold')
    
    # Set tick parameters for larger font size and integer formatting
    ax.tick_params(axis='both', which='major', labelsize=14)
    ax.tick_params(axis='both', which='minor', labelsize=12)
    
    # Format x-axis to show integers only
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{int(x)}'))
    
    # Format y-axis to show integers only
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{int(x)}'))
    
    # Add grid
    ax.grid(True, alpha=0.3)
    
    # Annual demand reference (Mt/year) on capacity axis; same series as boxplot demand lines
    demand_by_year = {
        2030: 29.0241717,
        2040: 15.0817033,
        2050: 11.6668363,
    }
    
    # Colors for demand lines
    demand_colors = {2030: 'red', 2040: 'orange', 2050: 'purple'}
    
    for year, demand in demand_by_year.items():
        if year in years:  # Only plot demand lines for years that have data
            ax.axvline(x=demand, color=demand_colors[year], linestyle='--', linewidth=2, alpha=0.8, 
                       label=f'{year} Demand: {demand:.0f} Mt/year')
    
    # Create legend
    legend_elements = []
    
    # Year legend
    for year in years:
        legend_elements.append(plt.Line2D([0], [0], marker='o', color='w', 
                                        markerfacecolor=year_colors[year], 
                                        markersize=15, label=f'{year}'))
    
    # Market legend
    for market in markets:
        market_desc = {'L': 'Low Market', 'M': 'Mid Market', 'H': 'High Market'}
        legend_elements.append(plt.Line2D([0], [0], marker=market_markers[market], 
                                        color='w', markerfacecolor='gray', 
                                        markersize=15, label=market_desc[market]))
    
    # Flexibility legend
    for flexibility in flexibilities:
        flex_desc = {'L': 'Low Flexibility', 'M': 'Mid Flexibility', 'H': 'High Flexibility', 'N': 'Non-constrained'}
        legend_elements.append(plt.Line2D([0], [0], marker='o', color='w', 
                                        markerfacecolor='w', markeredgecolor=flexibility_colors[flexibility],
                                        markersize=15, markeredgewidth=2, label=flex_desc[flexibility]))
    
    # Add demand lines to legend (only for years that have data)
    for year, demand in demand_by_year.items():
        if year in years:
            legend_elements.append(plt.Line2D([0], [0], color=demand_colors[year], linestyle='--', linewidth=2, 
                                            label=f'{year} Demand: {demand:.0f} Mt/year'))
    
    # Add legend
    ax.legend(handles=legend_elements, loc='upper right', fontsize=15, ncol=2)
    
    # Add labels for each point
    for point in optimal_points:
        year = point['year']
        market = point['market']
        flexibility = point['flexibility']
        capacity = point['capacity']
        net_value = point['net_value']
        
        ax.annotate(f'{year}-{flexibility}-{market}', 
                   xy=(capacity, net_value),
                   xytext=(5, 5), textcoords='offset points',
                   fontsize=12, alpha=0.8)
    
    plt.tight_layout()
    
    # Save plot
    output_dir = Path('results/optimal_points_analysis')
    output_dir.mkdir(parents=True, exist_ok=True)
    
    plot_file = output_dir / "optimal_points_scatter.png"
    plt.savefig(plot_file, dpi=300, bbox_inches='tight')
    logger.info(f"Optimal points scatter plot saved to: {plot_file}")
    
    # Show plot
    # plt.show()

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Plot optimal points distribution showing excess ratio and net value distribution')
    parser.add_argument('--results-dir', default='results', help='Results directory path (default: results)')
    parser.add_argument('--output', default='results/optimal_points_analysis', help='Output directory')
    parser.add_argument('--plot-type', choices=['distribution', 'scatter', 'boxplot'], default='distribution', 
                       help='Plot type: distribution (default), scatter, or boxplot')
    parser.add_argument('--no-cache', action='store_true', 
                       help='Disable cache and force recomputation of optimal points')
    parser.add_argument('--no-csv', action='store_true', 
                       help='Disable CSV output (only generate plots)')
    
    args = parser.parse_args()
    
    # Determine cache usage
    use_cache = not args.no_cache
    save_csv = not args.no_csv
    
    logger.info(f"Starting analysis of optimal points capacity and net value")
    logger.info(f"Results directory: {args.results_dir}")
    logger.info(f"Output directory: {args.output}")
    logger.info(f"Plot type: {args.plot_type}")
    logger.info(f"Use cache: {use_cache}")
    logger.info(f"Save CSV: {save_csv}")
    
    if args.plot_type == 'distribution':
        # Plot optimal points distribution
        plot_optimal_points_distribution(use_cache, save_csv)
    elif args.plot_type == 'scatter':
        # Plot optimal points scatter
        plot_optimal_points_scatter(use_cache, save_csv)
    elif args.plot_type == 'boxplot':
        # Plot optimal points box plot
        plot_optimal_points_boxplot(use_cache, save_csv)
    
    logger.info("Analysis completed!")

if __name__ == "__main__":
    main()