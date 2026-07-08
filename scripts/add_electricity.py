import logging
from _helpers import configure_logging, update_p_nom_max

import pypsa
import pandas as pd
import numpy as np
import xarray as xr
import geopandas as gpd

from vresutils import transfer as vtransfer

idx = pd.IndexSlice

logger = logging.getLogger(__name__)

def calculate_annuity(n, r):
    """Calculate the annuity factor for an asset with lifetime n years and
    discount rate of r, e.g. annuity(20, 0.05) * 20 = 1.6"""

    if isinstance(r, pd.Series):
        return pd.Series(1/n, index=r.index).where(r == 0, r/(1. - 1./(1.+r)**n))
    elif r > 0:
        return r / (1. - 1./(1.+r)**n)
    else:
        return 1 / n

def apply_market_scenario_costs(costs, config):
    """
    Adjust technology capital costs according to the market-opportunity scenario.

    Parameters
    ----------
    costs : pandas.DataFrame
        Technology cost table indexed by technology name.
    config : dict
        Configuration dictionary containing `aluminum.scenario_dimensions.market_opportunity`.

    Returns
    -------
    pandas.DataFrame
        Updated cost table with scenario-specific capital-cost multipliers applied.
    """
    # Apply cost multipliers based on the active market-opportunity scenario
    if 'aluminum' in config and 'current_scenario' in config['aluminum']:
        market_scenario = config['aluminum']['current_scenario'].get('market_opportunity', 'mid')
        
        if market_scenario in config['aluminum']['scenario_dimensions']['market_opportunity']:
            market_factors = config['aluminum']['scenario_dimensions']['market_opportunity'][market_scenario]
            
            # Apply VRE capital-cost multipliers.  Optional tech-specific factors
            # make it possible to test wind-vs-solar cost assumptions while keeping
            # existing configs backward-compatible with the aggregate VRE factor.
            vre_factor = float(market_factors.get('vre_cost_factor', 1.0))
            solar_factor = float(market_factors.get('solar_cost_factor', vre_factor))
            wind_factor = float(market_factors.get('wind_cost_factor', vre_factor))
            vre_tech_factors = {
                'solar': solar_factor,
                'solar-rooftop': solar_factor,
                'solar-utility': solar_factor,
                'onwind': wind_factor,
                'offwind': wind_factor,
            }
            for tech, factor in vre_tech_factors.items():
                if tech in costs.index:
                    costs.loc[tech, 'capital_cost'] *= factor
            
            # Apply battery capital-cost multiplier
            battery_techs = ['battery', 'battery storage', 'battery inverter']
            for tech in battery_techs:
                if tech in costs.index:
                    costs.loc[tech, 'capital_cost'] *= market_factors['battery_cost_factor']
            
            # Apply H2 / hydrogen-storage capital-cost multiplier
            h2_techs = ['H2', 'hydrogen storage tank type 1', 'fuel cell', 'electrolysis']
            for tech in h2_techs:
                if tech in costs.index:
                    costs.loc[tech, 'capital_cost'] *= market_factors['h2_cost_factor']
            
            # Apply methanation (Sabatier) capital-cost multiplier
            sabatier_techs = ['Sabatier']
            for tech in sabatier_techs:
                if tech in costs.index:
                    costs.loc[tech, 'capital_cost'] *= market_factors['sabatier_cost_factor']
            
            # Log the set of market-opportunity cost adjustments that were applied
            try:
                logger.info(f"Applied market opportunity cost factors for scenario '{market_scenario}': "
                           f"VRE={vre_factor}, "
                           f"Solar={solar_factor}, "
                           f"Wind={wind_factor}, "
                           f"Battery={market_factors['battery_cost_factor']}, "
                           f"H2={market_factors['h2_cost_factor']}, "
                           f"Sabatier={market_factors['sabatier_cost_factor']}")
            except Exception as e:
                print(f"WARNING: Logger failed: {e}")
    
    return costs


def load_costs(tech_costs, config, elec_config,cost_year, Nyears):

    # set all asset costs and other parameters
    costs = pd.read_csv(tech_costs, index_col=list(range(3))).sort_index()

    # correct units to MW and EUR
    costs.loc[costs.unit.str.contains("/kW"),"value"] *= 1e3
    costs.loc[costs.unit.str.contains("USD"),"value"] *= config['USD2013_to_EUR2013']

    cost_year = float(cost_year)
    costs = (costs.loc[idx[:,cost_year,:], "value"]
             .unstack(level=2).groupby("technology").sum(min_count=1))


    costs = costs.fillna({"CO2 intensity" : 0,
                          "FOM" : 0,
                          "VOM" : 0,
                          "discount rate" : config['discountrate'],
                          "efficiency" : 1,
                          "fuel" : 0,
                          "investment" : 0,
                          "lifetime" : 25})

    costs["capital_cost"] = ((calculate_annuity(costs["lifetime"], costs["discount rate"]) +
                             costs["FOM"]/100.) *
                             costs["investment"] * Nyears)

    costs.at['OCGT', 'fuel'] = costs.at['gas', 'fuel']
    costs.at['CCGT', 'fuel'] = costs.at['gas', 'fuel']

    costs['marginal_cost'] = costs['VOM'] + costs['fuel'] / costs['efficiency']

    costs = costs.rename(columns={"CO2 intensity": "co2_emissions"})

    costs.at['OCGT', 'co2_emissions'] = costs.at['gas', 'co2_emissions']
    costs.at['CCGT', 'co2_emissions'] = costs.at['gas', 'co2_emissions']

    costs.at['solar', 'capital_cost'] = 0.5*(costs.at['solar-rooftop', 'capital_cost'] +
                                             costs.at['solar-utility', 'capital_cost'])

    def costs_for_storage(store, link1, link2=None, max_hours=1.):
        capital_cost = link1['capital_cost'] + max_hours * store['capital_cost']
        if link2 is not None:
            capital_cost += link2['capital_cost']
        return pd.Series(dict(capital_cost=capital_cost,
                              marginal_cost=0.,
                              co2_emissions=0.))

    max_hours = elec_config['max_hours']
    costs.loc["battery"] = \
        costs_for_storage(costs.loc["battery storage"], costs.loc["battery inverter"],
                          max_hours=max_hours['battery'])
    costs.loc["H2"] = \
        costs_for_storage(costs.loc["hydrogen storage tank type 1"], costs.loc["fuel cell"],
                          costs.loc["electrolysis"], max_hours=max_hours['H2'])

    # Adjust costs according to market scenarios
    costs = apply_market_scenario_costs(costs, config)

    for attr in ('marginal_cost', 'capital_cost'):
        overwrites = config.get(attr)
        if overwrites is not None:
            overwrites = pd.Series(overwrites)
            costs.loc[overwrites.index, attr] = overwrites

    return costs

def update_transmission_costs(n, costs, length_factor=1.0):
    # TODO: line length factor of lines is applied to lines and links.
    # Separate the function to distinguish.

    n.lines['capital_cost'] = (n.lines['length'] * length_factor *
                               costs.at['HVAC overhead', 'capital_cost'])

    if n.links.empty: return

    dc_b = n.links.carrier == 'DC'

    # If there are no dc links, then the 'underwater_fraction' column
    # may be missing. Therefore we have to return here.
    if n.links.loc[dc_b].empty: return

    costs = (n.links.loc[dc_b, 'length'] * length_factor *
            ((1. - n.links.loc[dc_b, 'underwater_fraction']) *
            costs.at['HVDC overhead', 'capital_cost'] +
            n.links.loc[dc_b, 'underwater_fraction'] *
            costs.at['HVDC submarine', 'capital_cost']) +
            costs.at['HVDC inverter pair', 'capital_cost'])
    n.links.loc[dc_b, 'capital_cost'] = costs
