#!/usr/bin/env python3
# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
"""
Generate provincial aluminum demand and capacity data (units: ton/hour).

Output files:
1. aluminum_demand_by_province.csv - Provincial demand (by year and scenario), ton/hour
2. aluminum_capacity_by_province.csv - Provincial capacity, ton/hour
"""

import pandas as pd
import json
import numpy as np
from pathlib import Path

# File paths
BASE_DIR = Path(__file__).parent.parent
DEMAND_JSON = BASE_DIR / "data" / "aluminum_demand" / "aluminum_demand_all_scenarios.json"
CAPACITY_CSV = BASE_DIR / "data" / "p_nom" / "al_smelter_p_max.csv"
OUTPUT_DIR = BASE_DIR / "data" / "aluminum_demand"

# Conversion constants
HOURS_PER_YEAR = 8760  # hours per year
TONS_PER_10KT = 10000  # 10 kt = 10,000 tons


def load_demand_data():
    """Load national aluminum demand scenarios from JSON."""
    with open(DEMAND_JSON, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data['primary_aluminum_demand']


def load_capacity_data():
    """Load provincial smelter capacity data."""
    df = pd.read_csv(CAPACITY_CSV)
    df = df.set_index('Province')
    # Filter out provinces with zero/negligible capacity
    df = df[df['p_nom'] > 0.01]
    return df


def calculate_production_ratio(capacity_df):
    """Compute each province's production share based on installed capacity."""
    total_capacity = capacity_df['p_nom'].sum()
    production_ratio = capacity_df['p_nom'] / total_capacity
    return production_ratio


def convert_demand_to_tons_per_hour(demand_10kt):
    """
    Convert demand from 10 kt to ton/hour.
    
    Parameters
    ----------
    demand_10kt : float
        Demand in 10 kt (ten thousand tons).
    
    Returns
    -------
    float
        Demand in ton/hour.
    """
    demand_tons = demand_10kt * TONS_PER_10KT  # to tons
    demand_tons_per_hour = demand_tons / HOURS_PER_YEAR  # to ton/hour
    return demand_tons_per_hour


def convert_capacity_to_tons_per_hour(capacity_10kt_per_year):
    """
    Convert annual capacity from 10 kt/year to ton/hour.
    
    Parameters
    ----------
    capacity_10kt_per_year : float or pd.Series
        Annual output in 10 kt/year.
    
    Returns
    -------
    float or pd.Series
        Capacity in ton/hour.
    """
    capacity_tons_per_year = capacity_10kt_per_year * TONS_PER_10KT  # to tons/year
    capacity_tons_per_hour = capacity_tons_per_year / HOURS_PER_YEAR  # to ton/hour
    return capacity_tons_per_hour


def generate_demand_by_province():
    """Generate provincial demand data."""
    # Load data
    demand_data = load_demand_data()
    capacity_df = load_capacity_data()
    production_ratio = calculate_production_ratio(capacity_df)
    
    # Prepare output rows
    results = []
    
    # Iterate over all scenarios and years
    for scenario in ['low', 'mid', 'high']:
        if scenario not in demand_data:
            continue
        
        # Add 2025 demand (all scenarios are 40 Mt = 4000 * 10 kt)
        demand_data[scenario]['2025'] = 4000.0
        
        for year, demand_10kt in demand_data[scenario].items():
            # Convert to ton/hour
            national_demand_tons_per_hour = convert_demand_to_tons_per_hour(demand_10kt)
            
            # Allocate by province
            for province in production_ratio.index:
                province_demand = national_demand_tons_per_hour * production_ratio[province]
                results.append({
                    'Province': province,
                    'Year': year,
                    'Scenario': scenario,
                    'Demand_ton_per_h': province_demand
                })
    
    # Convert to DataFrame
    df = pd.DataFrame(results)
    
    # Reorder columns
    df = df[['Province', 'Year', 'Scenario', 'Demand_ton_per_h']]
    
    return df


def generate_capacity_by_province():
    """Generate provincial capacity data."""
    # Load data
    capacity_df = load_capacity_data()
    
    # Convert to ton/hour
    capacity_tons_per_hour = convert_capacity_to_tons_per_hour(capacity_df['p_nom'])
    
    # Create output DataFrame (reset index to avoid conflicts)
    df = pd.DataFrame({
        'Province': capacity_df.index,
        'Capacity_10kt_per_year': capacity_df['p_nom'].values,
        'Capacity_ton_per_h': capacity_tons_per_hour.values
    })
    
    # Sort by province
    df = df.sort_values('Province').reset_index(drop=True)
    
    return df


def main():
    """Main entry point."""
    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    print("Generating provincial aluminum demand data...")
    demand_df = generate_demand_by_province()
    demand_output = OUTPUT_DIR / "aluminum_demand_by_province.csv"
    demand_df.to_csv(demand_output, index=False, encoding='utf-8-sig')
    print(f"✓ Demand data saved to: {demand_output}")
    print(f"  {len(demand_df)} records")
    print(f"  Provinces: {demand_df['Province'].nunique()}")
    print(f"  Years: {sorted(demand_df['Year'].unique())}")
    print(f"  Scenarios: {sorted(demand_df['Scenario'].unique())}")

    print("\nGenerating provincial capacity data...")
    capacity_df = generate_capacity_by_province()
    capacity_output = OUTPUT_DIR / "aluminum_capacity_by_province.csv"
    capacity_df.to_csv(capacity_output, index=False, encoding='utf-8-sig')
    print(f"✓ Capacity data saved to: {capacity_output}")
    print(f"  {len(capacity_df)} provinces")
    print(f"  Total capacity: {capacity_df['Capacity_ton_per_h'].sum():.6f} ton/h")
    
    # Print some quick stats
    print("\n=== Demand data preview ===")
    print(demand_df.head(10))
    
    print("\n=== Capacity data preview ===")
    print(capacity_df.head(10))
    
    # Summarize demand by year
    print("\n=== National demand summary (ton/h) ===")
    demand_summary = demand_df.groupby(['Year', 'Scenario'])['Demand_ton_per_h'].sum()
    print(demand_summary)
    
    print("\nDone!")


if __name__ == "__main__":
    main()

