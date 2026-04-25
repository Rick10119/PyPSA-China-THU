# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
"""
Utility functions for handling scenario-dimension parameters.

These helpers read and interpret the scenario dimensions defined in `config.yaml`
for aluminum smelter flexibility, primary demand, market opportunity, and
employment-transfer assumptions.
"""

import yaml
from typing import Dict, Any, Optional
import json


def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    """
    Load a YAML configuration file.

    Args:
        config_path: Path to the configuration file.

    Returns:
        Parsed configuration dictionary.
    """
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def _get_market_key(config: Dict[str, Any]) -> str:
    scenario_dims = config.get('aluminum', {}).get('scenario_dimensions', {})
    if 'market_opportunity' in scenario_dims:
        return 'market_opportunity'
    return 'grid_interaction'


def _get_current_market_key(config: Dict[str, Any]) -> str:
    current = config.get('aluminum', {}).get('current_scenario', {})
    if 'market_opportunity' in current:
        return 'market_opportunity'
    return 'grid_interaction'


def get_scenario_params(config: Dict[str, Any], 
                       smelter_flexibility: str = None,
                       primary_demand: str = None, 
                       grid_interaction: str = None,
                       employment_transfer: str = None) -> Dict[str, Any]:
    """
    Retrieve the parameter bundles for a given scenario combination.

    Args:
        config: Full configuration dictionary.
        smelter_flexibility: Smelter flexibility scenario ('low', 'mid', 'high').
        primary_demand: Primary aluminum demand scenario ('low', 'mid', 'high').
        grid_interaction: Grid interaction / market opportunity scenario ('low', 'mid', 'high').
        employment_transfer: Employment transfer scenario ('unfavorable', 'favorable').

    Returns:
        Dictionary with sub-dictionaries for each scenario dimension.
    """
    # if not specified，Use default values ​​from current configuration
    current_scenario = config['aluminum']['current_scenario']
    market_key = _get_current_market_key(config)
    
    if smelter_flexibility is None:
        smelter_flexibility = current_scenario['smelter_flexibility']
    if primary_demand is None:
        primary_demand = current_scenario['primary_demand']
    if grid_interaction is None:
        grid_interaction = current_scenario[market_key]
    if employment_transfer is None:
        employment_transfer = current_scenario.get('employment_transfer')
    
    scenario_dims = config['aluminum']['scenario_dimensions']
    market_dim_key = _get_market_key(config)
    
    return {
        'smelter_flexibility': scenario_dims['smelter_flexibility'][smelter_flexibility],
        'primary_demand': scenario_dims['primary_demand'][primary_demand],
        'grid_interaction': scenario_dims[market_dim_key][grid_interaction],
        'employment_transfer': scenario_dims.get('employment_transfer', {}).get(employment_transfer, {}),
        'scenario_names': {
            'smelter_flexibility': smelter_flexibility,
            'primary_demand': primary_demand,
            'grid_interaction': grid_interaction,
            'employment_transfer': employment_transfer
        }
    }


def get_smelter_params(config: Dict[str, Any], scenario: str = None) -> Dict[str, Any]:
    """
    Get smelter-flexibility parameters for a given scenario.

    Args:
        config: Full configuration dictionary.
        scenario: Flexibility scenario ('low', 'mid', 'high').

    Returns:
        Dictionary with smelter parameters.
    """
    if scenario is None:
        scenario = config['aluminum']['current_scenario']['smelter_flexibility']
    
    return config['aluminum']['scenario_dimensions']['smelter_flexibility'][scenario]


def get_demand_params(config: Dict[str, Any], scenario: str = None) -> Dict[str, Any]:
    """
    Get primary-aluminum demand parameters for a given scenario.

    Args:
        config: Full configuration dictionary.
        scenario: Demand scenario ('low', 'mid', 'high').

    Returns:
        Dictionary with demand parameters.
    """
    if scenario is None:
        scenario = config['aluminum']['current_scenario']['primary_demand']
    
    return config['aluminum']['scenario_dimensions']['primary_demand'][scenario]


def get_grid_interaction_params(config: Dict[str, Any], scenario: str = None) -> Dict[str, Any]:
    """
    Get grid-interaction / market-opportunity parameters for a given scenario.

    Args:
        config: Full configuration dictionary.
        scenario: Grid-interaction scenario ('low', 'mid', 'high').

    Returns:
        Dictionary with grid-interaction parameters.
    """
    if scenario is None:
        market_key = _get_current_market_key(config)
        scenario = config['aluminum']['current_scenario'][market_key]
    
    market_dim_key = _get_market_key(config)
    return config['aluminum']['scenario_dimensions'][market_dim_key][scenario]


def get_employment_transfer_params(config: Dict[str, Any], scenario: str = None) -> Dict[str, Any]:
    """
    Get employment-transfer parameters for a given scenario.

    Args:
        config: Full configuration dictionary.
        scenario: Employment-transfer scenario ('unfavorable', 'favorable').

    Returns:
        Dictionary with employment-transfer parameters.
    """
    if scenario is None:
        scenario = config['aluminum']['current_scenario'].get('employment_transfer')
    
    return config['aluminum']['scenario_dimensions'].get('employment_transfer', {}).get(scenario, {})


def list_all_scenarios() -> Dict[str, list]:
    """
    List all available scenario labels for each dimension.

    Returns:
        Dictionary mapping dimension name to list of scenario labels.
    """
    return {
        'smelter_flexibility': ['low', 'mid', 'high'],
        'primary_demand': ['low', 'mid', 'high'],
        'grid_interaction': ['low', 'mid', 'high'],
        'employment_transfer': ['unfavorable', 'favorable']
    }


def generate_scenario_combinations() -> list:
    """
    Generate all possible combinations across the scenario dimensions.

    Returns:
        List of scenario-combination dictionaries.
    """
    scenarios = list_all_scenarios()
    combinations = []
    
    for sf in scenarios['smelter_flexibility']:
        for pd in scenarios['primary_demand']:
            for gi in scenarios['grid_interaction']:
                for et in scenarios['employment_transfer']:
                    combinations.append({
                        'smelter_flexibility': sf,
                        'primary_demand': pd,
                        'grid_interaction': gi,
                        'employment_transfer': et,
                        'name': f"{sf}-{pd}-{gi}-{et}"
                    })
    
    return combinations


def get_aluminum_smelter_operational_params(config: Dict[str, Any], 
                                          smelter_flexibility: str = None,
                                          al_smelter_p_nom: float = None) -> Dict[str, Any]:
    """
    Build operational parameters for the aluminum smelter links.

    This includes restart, stand-by, and marginal costs consistent with the
    scenario settings in `config.yaml`.

    Args:
        config: Full configuration dictionary.
        smelter_flexibility: Smelter flexibility scenario ('low', 'mid', 'high').
        al_smelter_p_nom: Smelter capacity (MW), used to scale absolute costs.

    Returns:
        Dictionary with operational parameters for the smelter links.
    """
    # Obtain flexibility parameters of electrolytic aluminum plant
    smelter_params = get_smelter_params(config, smelter_flexibility)
    
    # Employment transfer parameters（Affect stand_by_cost）
    employment_params = get_employment_transfer_params(config)

    # Basic parameters
    operational_params = {
        'p_min_pu': smelter_params['p_min_pu'],
        'capital_cost': 33234.9, # eur/MW, 400 ￥/tonne
        'stand_by_cost': smelter_params.get('stand_by_cost', 1.4227),  # eur/MW/h, 150 ￥/tonne
        'marginal_cost': 1.4227, # eur/MWh, 150 ￥/tonne
    }

    if employment_params:
        if 'stand_by_cost' in employment_params:
            operational_params['stand_by_cost'] = employment_params['stand_by_cost']
        elif 'stand_by_cost_factor' in employment_params:
            operational_params['stand_by_cost'] = (
                operational_params['stand_by_cost'] * employment_params['stand_by_cost_factor']
            )
    
    # If capacity is provided，Calculate cost parameters
    if al_smelter_p_nom is not None:
        operational_params.update({
            'start_up_cost': smelter_params['restart_cost'] * al_smelter_p_nom,
            'shut_down_cost': 0,
            'stand_by_cost': operational_params['stand_by_cost'] * al_smelter_p_nom,
        })
    else:
        # Report an error
        raise ValueError("al_smelter_p_nom is required")
    
    return operational_params


def get_aluminum_demand_for_year(config: Dict[str, Any], 
                                year: str,
                                primary_demand_scenario: str = None,
                                aluminum_demand_json_path: str = "data/aluminum_demand/aluminum_primary_demand_all_scenarios.json") -> float:
    """
    Look up primary-aluminum demand for a given year and demand scenario.

    Args:
        config: Full configuration dictionary.
        year: Year label (e.g. '2030', '2050').
        primary_demand_scenario: Demand scenario ('low', 'mid', 'high').
        aluminum_demand_json_path: Path to JSON file with demand trajectories.

    Returns:
        Total primary-aluminum demand in tonnes.
    """
    if primary_demand_scenario is None:
        primary_demand_scenario = config['aluminum']['current_scenario']['primary_demand']
    
    # Load aluminum demand data
    with open(aluminum_demand_json_path, 'r', encoding='utf-8') as f:
        aluminum_demand_data = json.load(f)
    
    # Get demand data (10kt)
    primary_demand_10kt = aluminum_demand_data['primary_aluminum_demand'][primary_demand_scenario][year]
    
    # Convert to tons
    primary_demand_tons = primary_demand_10kt * 10000
    
    return primary_demand_tons


def get_aluminum_load_for_network(config: Dict[str, Any],
                                 year: str,
                                 network_snapshots,
                                 nodes,
                                 production_ratio,
                                 primary_demand_scenario: str = None,
                                 aluminum_demand_json_path: str = "data/aluminum_demand/aluminum_primary_demand_all_scenarios.json") -> Dict[str, Any]:
    """
    Construct aluminum-load time series for the network model.

    Args:
        config: Full configuration dictionary.
        year: Year label used for demand.
        network_snapshots: Network snapshot index.
        nodes: List or index of provincial nodes.
        production_ratio: Provincial production share used to spatially allocate demand.
        primary_demand_scenario: Demand scenario.
        aluminum_demand_json_path: Path to JSON file with demand trajectories.

    Returns:
        Dictionary with provincial aluminum load and aggregate quantities.
    """
    # Obtain primary aluminum demand
    primary_demand_tons = get_aluminum_demand_for_year(
        config, year, primary_demand_scenario, aluminum_demand_json_path
    )
    
    # Calculate national aluminum load (MW)
    hours_per_year = 8760
    national_al_load = primary_demand_tons / hours_per_year
    
    # Create aluminum load time series
    al_load_values = np.tile(
        national_al_load * production_ratio.values,
        (len(network_snapshots), 1)
    )
    
    aluminum_load = pd.DataFrame(
        data=al_load_values,
        index=network_snapshots,
        columns=production_ratio.index
    )
    
    return {
        'aluminum_load': aluminum_load,
        'national_al_load': national_al_load,
        'primary_demand_tons': primary_demand_tons
    }


def get_current_scenario_name(config: Dict[str, Any]) -> str:
    """
    Build a human-readable name for the current scenario combination.

    Args:
        config: Full configuration dictionary.

    Returns:
        Scenario-name string (e.g. 'mid-mid-mid' or 'mid-mid-high-favorable').
    """
    current = config['aluminum']['current_scenario']
    market_key = _get_current_market_key(config)
    employment = current.get('employment_transfer')
    if employment:
        return f"{current['smelter_flexibility']}-{current['primary_demand']}-{current[market_key]}-{employment}"
    return f"{current['smelter_flexibility']}-{current['primary_demand']}-{current[market_key]}"


def validate_scenario_params(config: Dict[str, Any], 
                           smelter_flexibility: str = None,
                           primary_demand: str = None,
                           grid_interaction: str = None,
                           employment_transfer: str = None) -> bool:
    """
    Validate whether the provided scenario labels are allowed.

    Args:
        config: Full configuration dictionary.
        smelter_flexibility: Smelter flexibility scenario label.
        primary_demand: Primary demand scenario label.
        grid_interaction: Grid-interaction / market-opportunity label.
        employment_transfer: Employment-transfer scenario label.

    Returns:
        True if all provided labels are valid, False otherwise.
    """
    valid_flex = ['low', 'mid', 'high', 'non_constrained']
    valid_demand = ['low', 'mid', 'high']
    valid_market = ['low', 'mid', 'high']
    valid_employment = ['unfavorable', 'favorable']
    
    if smelter_flexibility and smelter_flexibility not in valid_flex:
        return False
    if primary_demand and primary_demand not in valid_demand:
        return False
    if grid_interaction and grid_interaction not in valid_market:
        return False
    if employment_transfer and employment_transfer not in valid_employment:
        return False
    
    return True


def print_scenario_summary(config: Dict[str, Any], scenario_params: Dict[str, Any]):
    """
    Print a concise, English-language summary of the active scenario parameters.

    Args:
        config: Full configuration dictionary.
        scenario_params: Scenario-parameter bundle returned by `get_scenario_params`.
    """
    print("=== Scenario summary ===")
    print(f"Scenario combination: {get_current_scenario_name(config)}")
    
    # Smelter-operational parameters
    smelter = scenario_params['smelter_flexibility']
    print("\nSmelter operating flexibility:")
    print(f"  Minimum power: {smelter['p_min']}")
    print(f"  Start-up cost: {smelter['start_up_cost']} $/MW")
    print(f"  Shut-down cost: {smelter['shut_down_cost']} $/MW")
    
    # Demand-side parameters
    demand = scenario_params['primary_demand']
    print("\nPrimary aluminum demand:")
    print(f"  Domestic demand share: {demand['domestic_demand_ratio']}")
    print(f"  Export rate: {demand['export_rate']}")
    print(f"  Recycling rate: {demand['recycling_rate']}")
    print(f"  Product lifetime: {demand['product_lifetime']} years")
    
    # Grid-interaction / technology-cost parameters
    grid = scenario_params['grid_interaction']
    print("\nGrid-interaction / market opportunity:")
    print(f"  VRE cost reduction: {grid['vre_cost_reduction']*100}%")
    print(f"  Battery cost reduction: {grid['battery_cost_reduction']*100}%")
    print(f"  H2 storage cost reduction: {grid['h2_storage_cost_reduction']*100}%")
    print(f"  Other flexible demand: {grid['other_flexible_demand']*100}%")


# Add necessary imports
import numpy as np
import pandas as pd