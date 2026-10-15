# Aluminum Demand and Capacity Integration Guide

## 1. Overview

This document details how aluminum smelting demand data and installed capacity are integrated into the PyPSA-China model. As a highly energy-intensive industrial load, aluminum smelting is represented through the following mechanisms:

- **Demand data**: primary aluminum demand by year is read from scenario data files.
- **Installed capacity**: provincial smelter capacity is read from the existing smelter dataset.
- **Model components**: Links, Stores, and Loads represent aluminum in the PyPSA network.
- **Optimization algorithm**: an iterative algorithm handles unit-commitment constraints and flexibility.

> **Scope note:** This repository contains the reduced-form/aggregated aluminum-load representation and its integration into the PyPSA-China planning model, including a price-based iterative solve with provincial representative-line MILP sub-problems. The detailed aluminum-smelter physical model and the D3R dimensional-reduction/training workflow are outside this repository. References: *Thermal-Mass Coupled Modeling and Optimal Scheduling of Aluminum Smelting Loads for Safe Demand Response Participation* (IEEE Transactions on Smart Grid, 2026), DOI: https://doi.org/10.1109/TSG.2026.3736640; *Data-Driven Dimension Reduction for Industrial Load Modeling Using Inverse Optimization* (IEEE Transactions on Smart Grid, 2025), DOI: https://doi.org/10.1109/TSG.2025.3545339; D3R implementation: https://github.com/Rick10119/Data-Driven-Dimension-Reduction.

## 2. Data Sources

### 2.1 Demand Data

**File path**: `data/aluminum_demand/aluminum_demand_all_scenarios.json`

**Data structure**:
```json
{
  "primary_aluminum_demand": {
    "low": {
      "2030": 1909.875997700051,
      "2050": 628.2308472342488,
      ...
    },
    "mid": {...},
    "high": {...}
  }
}
```

**Notes**:
- Three demand scenarios: `low`, `mid`, `high`.
- Years covered: 2030–2060.
- Unit: 10 kt (10,000 tonnes).
- Source: EP model outputs.

### 2.2 Capacity Data

**File path**: `data/p_nom/al_smelter_p_max.csv`

**Data structure**:
```csv
Province,p_nom
Anhui,161.492652
Gansu,300.120354
Shandong,505.749267
...
```

**Notes**:
- Unit: 10 kt/yr.
- Contains annual aluminum production by province.
- Used to compute provincial capacity and production ratio.

## 3. Demand Data Processing

### 3.1 Reading Demand Data

Demand data is read via functions in `scripts/scenario_utils.py`:

```python
def get_aluminum_demand_for_year(config, year, primary_demand_scenario=None,
                                  aluminum_demand_json_path=...):
    """
    Retrieve primary aluminum demand for a given year and scenario.

    Returns: primary aluminum demand in tonnes.
    """
    # 1. Obtain demand scenario from config (or use current_scenario).
    # 2. Read the corresponding year and scenario from the JSON file (10 kt).
    # 3. Convert to tonnes: primary_demand_tons = primary_demand_10kt * 10000.
    return primary_demand_tons
```

**Key code location**: `scripts/scenario_utils.py:195-224`

### 3.2 Converting Demand to Load

Demand is converted to network load via `get_aluminum_load_for_network()`:

```python
def get_aluminum_load_for_network(config, year, network_snapshots, nodes,
                                  production_ratio, ...):
    """
    Convert primary aluminum demand to network load.

    Steps:
    1. Retrieve primary demand (tonnes).
    2. Compute national average aluminum load: national_al_load = primary_demand_tons / 8760 (MW).
    3. Distribute across provinces by production_ratio.
    4. Create time-series load data.
    """
    hours_per_year = 8760
    national_al_load = primary_demand_tons / hours_per_year

    al_load_values = np.tile(
        national_al_load * production_ratio.values,
        (len(network_snapshots), 1)
    )

    return {
        'aluminum_load': aluminum_load,       # DataFrame: snapshots × provinces
        'national_al_load': national_al_load,
        'primary_demand_tons': primary_demand_tons
    }
```

**Key code location**: `scripts/scenario_utils.py:227-274`

**Conversion formulas**:
- National average load (MW) = primary demand (tonnes) / 8760 hours
- Provincial load = national average load × provincial production_ratio

## 4. Capacity Data Processing

### 4.1 Reading Capacity Data

**Code location**: `scripts/prepare_base_network.py:201-224`

```python
al_smelter_annual_production = pd.read_csv(snakemake.input.al_smelter_p_max)
al_smelter_annual_production = al_smelter_annual_production.set_index('Province')['p_nom']

# Keep only provinces with annual production > 0.01 (10 kt/yr)
al_smelter_annual_production = al_smelter_annual_production[
    al_smelter_annual_production > 0.01
]

# Compute production ratio
production_ratio = al_smelter_annual_production / al_smelter_annual_production.sum()
```

### 4.2 Capacity Conversion

**Code location**: `scripts/prepare_base_network.py:210-219`

```python
# Convert to power capacity (MW)
# 1. Annual production (10 kt/yr) → tonnes/yr = production × 10000
# 2. tonnes/yr → MWh/yr = tonnes × 13.3   (1 tonne Al ≈ 13.3 MWh)
# 3. MWh/yr → MW = MWh / 8760

base_capacity = al_smelter_annual_production * 10000 * 13.3 / 8760

# Apply configurable capacity ratio
capacity_ratio = config['aluminum']['capacity_ratio']  # default 1.0
al_smelter_p_nom = base_capacity * capacity_ratio
```

**Summary formula**:
```
p_nom (MW) = annual_production (10 kt/yr) × 10000 × 13.3 / 8760 × capacity_ratio
```

**Parameter notes**:
- `13.3`: electricity consumption per tonne of aluminum (MWh/t).
- `8760`: hours per year.
- `capacity_ratio`: configurable in `config.yaml` (default 1.0).

## 5. Model Component Integration

### 5.1 Adding Network Components

**Code location**: `scripts/prepare_base_network.py:243-283`

#### 5.1.1 Aluminum Smelter (Link)

```python
network.madd("Link",
            production_ratio.index,
            suffix=" aluminum smelter",
            bus0=production_ratio.index,                   # electricity bus (input)
            bus1=production_ratio.index + " aluminum",     # aluminum bus (output)
            carrier="aluminum",
            p_nom=al_smelter_p_nom,
            p_nom_extendable=False,
            efficiency=1.0/13.3,      # 1 MW electricity → 1/13.3 t Al/h
            capital_cost=operational_params['capital_cost'],
            stand_by_cost=operational_params['stand_by_cost'],
            marginal_cost=operational_params['marginal_cost'],
            start_up_cost=0.5*operational_params['start_up_cost'],
            shut_down_cost=0.5*operational_params['start_up_cost'],
            committable=config['aluminum_commitment'],
            p_min_pu=operational_params['p_min_pu'] if config['aluminum_commitment'] else 0,
)
```

**Key parameters**:
- `efficiency = 1.0/13.3`: 1 MW input produces 1/13.3 t Al/h.
- `p_nom`: installed capacity computed from data.
- `committable`: enables unit-commitment constraints (MILP).

#### 5.1.2 Aluminum Storage (Store)

```python
network.madd("Store",
            production_ratio.index,
            suffix=" aluminum storage",
            bus=production_ratio.index + " aluminum",
            carrier="aluminum",
            e_nom_extendable=True,
            e_cyclic=True)
```

**Purpose**: allows aluminum to be shifted in time, providing operational flexibility.

#### 5.1.3 Aluminum Load (Load)

```python
network.madd("Load",
            production_ratio.index,
            suffix=" aluminum",
            bus=production_ratio.index + " aluminum",
            p_set=aluminum_load[production_ratio.index])
```

**Purpose**: represents provincial aluminum demand.

#### 5.1.4 Electricity Load Adjustment

```python
# Subtract aluminum load from electricity load to avoid double-counting
load_minus_al = load.copy()
load_minus_al[production_ratio.index] = (
    load[production_ratio.index] -
    aluminum_load[production_ratio.index] * 10000 * 13.3 / 8760
)
network.madd("Load", nodes, bus=nodes, p_set=load_minus_al)
```

**Rationale**: aluminum load is modeled separately and must be removed from the aggregate electricity load.

#### 5.1.5 China Aluminum Hub

```python
# National aluminum hub bus for inter-provincial aluminum transfer
network.add("Bus", "China aluminum hub", carrier="aluminum transfer")

# Bidirectional transfer links
for province in production_ratio.index:
    # Province → hub
    network.add("Link", f"{province} to China aluminum hub", ...)
    # Hub → province
    network.add("Link", f"China aluminum hub to {province}", ...)
```

**Purpose**: enables inter-provincial aluminum transfer to satisfy the national demand constraint.

### 5.2 Operational Parameters

**Code location**: `scripts/scenario_utils.py:153-192`

Operational parameters are set according to the selected scenario dimension:

```python
def get_aluminum_smelter_operational_params(config, smelter_flexibility=None,
                                            al_smelter_p_nom=None):
    """
    Retrieve aluminum smelter operational parameters.

    Source: config['aluminum']['scenario_dimensions']['smelter_flexibility']
    """
    smelter_params = get_smelter_params(config, smelter_flexibility)

    return {
        'p_min_pu': smelter_params['p_min_pu'],
        'capital_cost': 163432.8,
        'stand_by_cost': smelter_params['stand_by_cost'] * al_smelter_p_nom,  # $/h
        'marginal_cost': 1,
        'start_up_cost': smelter_params['restart_cost'] * al_smelter_p_nom,
    }
```

**Scenario parameters** (from `config.yaml`):
- `low`: p_min_pu = 0.9, restart_cost = 110 000 $/MW
- `mid`: p_min_pu = 0.7, restart_cost = 16 000 $/MW
- `high`: p_min_pu = 0.5, restart_cost = 3 200 $/MW
- `non_constrained`: p_min_pu = 0.0, restart_cost = 0

## 6. Iterative Optimization Algorithm

### 6.1 Algorithm Overview

Because aluminum unit-commitment constraints introduce integer variables (MILP), an iterative optimization algorithm is used:

**Code location**: `scripts/solve_network_myopic.py:583-1163`

**Algorithm flow**:

1. **Initialization**: aluminum power-consumption profile set to empty.
2. **Iterative solve**:
   - **Step 1**: Solve the relaxed (continuous) aluminum model to obtain nodal prices and the objective value.
   - **Step 2**: Based on nodal prices, solve the aluminum optimal-dispatch MILP sub-problem to obtain a new consumption profile.
   - **Step 3**: Fix aluminum consumption via `p_set` and re-solve the network.
   - **Step 4**: Check objective convergence.
3. **Convergence check**: relative change in the objective < threshold (default 1 %).
4. **Output**: return the final network.

### 6.2 Key Implementation Details

#### 6.2.1 Network Reload

The network is reloaded from disk at each iteration to keep state clean:

```python
if original_network_path:
    n_current = pypsa.Network(original_network_path, override_component_attrs=overrides)
else:
    n_current = copy.deepcopy(original_network)

n_current = prepare_network(n_current, ...)
```

#### 6.2.2 Fixing Aluminum Consumption

Aluminum consumption is fixed via `p_set` rather than explicit constraints:

```python
for smelter in aluminum_usage.columns:
    if smelter in n_current.links.index:
        fixed_aluminum_power = aluminum_usage[smelter].values
        n_current.links_t.p_set[smelter] = fixed_aluminum_power

        for load in aluminum_loads:
            n_current.loads_t.p_set[load] = fixed_aluminum_power
```

#### 6.2.3 Nodal Price Extraction

Nodal prices are extracted from the relaxed-model solution:

```python
if hasattr(n_current, 'buses_t') and hasattr(n_current.buses_t, 'marginal_price'):
    electricity_buses = n_current.buses[n_current.buses.carrier != "aluminum"].index
    current_nodal_prices = n_current.buses_t.marginal_price[electricity_buses]
```

#### 6.2.4 Aluminum Optimization Sub-problem

The aluminum optimal-dispatch sub-problem is solved based on nodal prices:

```python
def solve_aluminum_optimization(n, config, solving, opts="",
                                nodal_prices=None, target_province=None, ...):
    """
    Aluminum optimal-dispatch sub-problem.

    Objective: minimize total cost (electricity + start-up/shut-down).
    Constraints:
    1. Meet aluminum demand.
    2. Capacity limits.
    3. Unit-commitment constraints (if enabled).
    """
    # Build simplified aluminum optimization network.
    # Add virtual generators (marginal cost = nodal price).
    # Solve MILP.
    # Return aluminum consumption profile.
```

## 7. Configuration Parameters

### 7.1 Basic Configuration

**File**: `config.yaml`

```yaml
aluminum:
  # Grid-interaction toggle (by year)
  grid_interaction:
    "2020": false
    "2030": true
    "2050": true
    ...

  # Smelter capacity ratio
  capacity_ratio: 1.0  # 1.0 = 100 %, 0.9 = 90 %, ...

  # Currently selected scenario
  current_scenario:
    smelter_flexibility: "low"
    primary_demand: "high"
    market_opportunity: "high"
```

### 7.2 Scenario Dimension Parameters

```yaml
aluminum:
  scenario_dimensions:
    smelter_flexibility:
      low:
        p_min_pu: 0.9          # minimum output ratio
        restart_cost: 110000   # restart cost ($/MW)
        stand_by_cost: 1.5     # stand-by cost ($/MW/h)
      mid:
        p_min_pu: 0.7
        restart_cost: 16000
        stand_by_cost: 1.5
      high:
        p_min_pu: 0.5
        restart_cost: 3200
        stand_by_cost: 1.5
```

### 7.3 Iteration Parameters

```yaml
# Set inside solve_opts
aluminum_max_iterations: 10              # maximum iterations
aluminum_convergence_tolerance: 0.01     # convergence threshold (1 %)
aluminum_commitment: false                # enable unit-commitment constraints
```

## 8. Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        Data Input                           │
├─────────────────────────────────────────────────────────────┤
│ 1. Demand data: aluminum_demand_all_scenarios.json          │
│    └─> primary demand (10 kt) × scenario × year            │
│                                                             │
│ 2. Capacity data: al_smelter_p_max.csv                     │
│    └─> provincial annual production (10 kt/yr)              │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                     Data Processing                         │
├─────────────────────────────────────────────────────────────┤
│ 1. Demand processing:                                       │
│    primary_demand (10 kt)                                   │
│    └─> primary_demand_tons (tonnes)                         │
│        └─> national_al_load (MW)                            │
│            └─> distribute by production_ratio               │
│                └─> aluminum_load (time series × province)   │
│                                                             │
│ 2. Capacity processing:                                     │
│    al_smelter_annual_production (10 kt/yr)                  │
│    └─> base_capacity (MW) = production × 10000 × 13.3/8760 │
│        └─> al_smelter_p_nom (MW) = base_capacity × ratio   │
│                                                             │
│ 3. Ratio calculation:                                       │
│    production_ratio = provincial / national total            │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                 Network Component Addition                   │
├─────────────────────────────────────────────────────────────┤
│ 1. Link: aluminum smelter                                   │
│    bus0: electricity bus → bus1: aluminum bus                │
│    p_nom: installed capacity, efficiency: 1/13.3            │
│                                                             │
│ 2. Store: aluminum storage                                  │
│    bus: aluminum bus, e_nom_extendable: True                │
│                                                             │
│ 3. Load: aluminum load                                      │
│    bus: aluminum bus, p_set: time-series load               │
│                                                             │
│ 4. Load: electricity load (aluminum subtracted)             │
│                                                             │
│ 5. Bus + Link: China Aluminum Hub (inter-provincial)        │
└─────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                   Iterative Optimization                    │
├─────────────────────────────────────────────────────────────┤
│ Iteration 1:                                                │
│   1. Relaxed model solve → nodal prices                     │
│   2. MILP solve on nodal prices → consumption profile       │
│   3. Fix consumption, re-solve network                      │
│   4. Check convergence                                      │
│                                                             │
│ Iterations 2–N:                                             │
│   Repeat until converged                                    │
└─────────────────────────────────────────────────────────────┘
```

## 9. Key Formulas

### 9.1 Capacity Conversion

```
p_nom (MW) = annual_production (10 kt/yr) × 10000 × 13.3 / 8760 × capacity_ratio
```

Where:
- `10000`: converts 10 kt to tonnes.
- `13.3`: electricity per tonne of aluminum (MWh/t).
- `8760`: hours per year.
- `capacity_ratio`: configurable (default 1.0).

### 9.2 Demand Conversion

```
National average load (MW) = primary demand (tonnes) / 8760
Provincial load (MW) = national average load × production_ratio
```

### 9.3 Efficiency Parameter

```
Link efficiency = 1.0 / 13.3
Meaning: 1 MW electricity input → 1/13.3 t Al/h output
```

## 10. Related Files

### 10.1 Data Files
- `data/aluminum_demand/aluminum_demand_all_scenarios.json` – demand data
- `data/p_nom/al_smelter_p_max.csv` – capacity data

### 10.2 Code Files
- `scripts/prepare_base_network.py` – network preparation, adds aluminum components
- `scripts/scenario_utils.py` – scenario utilities, demand/capacity processing
- `scripts/solve_network_myopic.py` – iterative optimization implementation
- `config.yaml` – configuration file

### 10.3 Documentation
- `docs/README_aluminum_iterative.md` – iterative algorithm notes
- `docs/aluminum_integration_guide.md` – this document

## 11. Usage Examples

### 11.1 Enable Aluminum

In `config.yaml`:

```yaml
add_aluminum: true
aluminum:
  grid_interaction:
    "2030": true
  capacity_ratio: 1.0
  current_scenario:
    smelter_flexibility: "mid"
    primary_demand: "mid"
```

### 11.2 Adjust Capacity Ratio

```yaml
aluminum:
  capacity_ratio: 0.8  # use 80 % of capacity
```

### 11.3 Set Iteration Parameters

```yaml
solve_opts:
  aluminum_max_iterations: 10
  aluminum_convergence_tolerance: 0.01
  aluminum_commitment: true  # enable unit commitment
```

## 12. Notes

1. **Unit conversion**: be careful with units (10 kt vs tonnes, MW vs MWh).
2. **Time series**: the aluminum load is constant across all hours (no variation).
3. **Province filtering**: components are only added for provinces with annual production > 0.01 (10 kt/yr).
4. **Electricity load adjustment**: aluminum load must be subtracted from the aggregate electricity load to avoid double-counting.
5. **Iteration convergence**: if the algorithm does not converge, check the threshold and maximum iteration count.
6. **MILP solver**: when unit commitment is enabled, a MILP-capable solver (e.g., Gurobi) is required.

## 13. FAQ

### Q1: How do I change the aluminum demand scenario?
A: Set `aluminum.current_scenario.primary_demand` in `config.yaml` to `low`, `mid`, or `high`.

### Q2: How do I adjust smelter capacity?
A: Change `aluminum.capacity_ratio` in `config.yaml` (range 0.0–1.0).

### Q3: What if the iteration does not converge?
A: Check the following:
- Convergence threshold (recommended 0.01–0.05).
- Maximum iterations (recommended 10–20).
- Solver settings.

### Q4: How do I disable the aluminum feature?
A: Set `add_aluminum: false` or `aluminum.grid_interaction[year]: false`.

---

**Document version**: v1.0
**Last updated**: 2025
**Maintainer**: Ruike Lyu
