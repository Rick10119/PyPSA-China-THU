# PyPSA-China: An Open Optimization Model of the Chinese Energy System

PyPSA-China is an open-source capacity expansion and operational optimization model for the Chinese energy system, built on the [PyPSA](https://pypsa.org/) framework. It covers electricity, heating, gas, and hydrogen carriers at provincial resolution and features a dedicated module for modeling aluminum smelter flexibility as a demand-side resource in high-renewable grids.

## Motivation

China's power system is undergoing a rapid transition toward variable renewable energy (VRE). At the same time, the country operates roughly 45 Mt of primary aluminum smelting capacity — one of the single largest electricity loads in any national grid. PyPSA-China brings these two dimensions together: it co-optimizes generation, storage, and transmission investment alongside aluminum smelter scheduling, showing how industrial overcapacity can provide seasonal flexibility and significantly reduce system costs.

## Key Features

- **Multi-sector energy system**: integrated modeling of electricity, centralized/decentralized heating, gas, coal, and hydrogen.
- **Provincial resolution**: 30-province transmission network with inter-provincial transfer capacities.
- **Myopic capacity expansion**: sequential planning across multiple horizons (e.g., 2020 → 2030 → 2040 → 2050), carrying forward brownfield capacity.
- **Aluminum smelter integration**: potline-level unit-commitment sub-problem solved iteratively against the main dispatch/investment problem via nodal-price decomposition.
- **Three-dimensional scenario framework**: smelter flexibility × primary demand × grid-interaction market opportunity, each at low / mid / high levels (27 combinations).
- **Configurable capacity ratios**: aluminum smelter capacity can be scaled from 5 % to 100 % of the installed base to explore overcapacity effects.
- **HPC support**: automated SLURM job generation for large-scale scenario sweeps across 1 000+ configurations.

## Workflow

The Snakemake pipeline proceeds in five stages:

```
prepare_base_networks_2020   (base-year network with existing infrastructure)
        │
        ▼
prepare_base_networks        (future-year networks with updated costs and potentials)
        │
        ▼
add_existing_baseyear        (attach existing generators, storage, transmission for 2020)
        │
        ▼
add_brownfield               (carry forward solved capacity from previous horizon)
        │
        ▼
solve_network_myopic         (optimize dispatch + investment; aluminum iterative loop)
```

Each stage reads from `config.yaml` and data files under `data/`, and writes intermediate or final networks to `results/`.

### Heat-only workflow note (deprecated)

Older versions of this repo included an experimental **heat-only** workflow that relied on **exogenous electricity prices**. That approach is deprecated in this repo; electricity price analysis should use post-processing based on solved networks (see `scripts/reconstruct_market_prices.py`).

### Synchronous-generation floor and mapped-price sidecar

The planning solve (`scripts/solve_network_myopic.py`) and the fixed-capacity dispatch solve
(`scripts/run_dispatch_segmented_prices.py`) can enforce a provincial synchronous-generation floor.
With the current default configuration, coal, nuclear, gas, and biomass synchronous generators must
produce at least `10 %` of the local AC electricity load in each province and snapshot:

```yaml
synchronous_generation_floor:
  enabled: true
  ratio: 0.10
```

The dispatch price export still writes the primary CSV from solved marginal prices
(`buses_t.marginal_price`). In addition, `scripts/export_reconstructed_prices.py` writes a
`*_mapped.csv` sidecar used by solar value-factor analysis. The mapped sidecar is reconstructed in
this order:

1. Build a local mapped price from thermal/synchronous output and the fuel-based supply curve.
   The biweekly normalization denominator is:
   `max(biweekly_max_thermal_output, biweekly_min_thermal_output / lr_threshold_first)`.
   With the default `lr_threshold_first: 0.4`, a stable must-run floor maps to the first supply
   band instead of being normalized to the peak band.
2. Apply the local must-run floor price rule before any cross-province transmission adjustment:
   - at or below `10 %` of local AC load, use the province's marginal price;
   - within `1.5 x` the floor (`15 %` of local AC load by default), cap mapped prices at the
     `1.0 x` reference fuel price.
3. Apply cross-province export adjustment. Only province-to-province links are considered
   (`bus0` and `bus1` both provincial AC buses). If province A exports to province B on an
   uncongested link, A's mapped price can be lifted to the receiving-side marginal price adjusted by
   link efficiency. The receiving province keeps its own local/floor-adjusted price; import flows do
   not lower or raise the receiving province's price.

Related configuration lives under `dispatch_segmented_prices.price_export`:

```yaml
dispatch_segmented_prices:
  price_export:
    week_freq: "2W-SUN"
    thermal_load_floor:
      enabled: true
      ratio: 0.10
    mapped_supply_curve:
      lr_threshold_first: 0.4
```

## Installation

### Prerequisites

- Python 3.9+ (see `envs/environment.yaml`)
- Gurobi Optimizer with a valid license (required to reproduce all scenarios in the paper)
- Sufficient memory (20–100 GB depending on network size)

### Environment Setup

```bash
git clone https://github.com/your-repo/PyPSA-China.git
cd PyPSA-China

conda env create -f envs/environment.yaml
conda activate pypsa-china
```

Note:
- The Snakemake scripts in this repo are written against the project's original PyPSA stack (e.g. PyPSA ~0.29). If you create an environment with a much newer PyPSA, you may hit import errors such as `ImportError: cannot import name 'Dict' from pypsa.descriptors` in `scripts/_helpers.py`.

### Solver and Licensing

- **Default solver**: Gurobi (`solving.solver.name: gurobi` in `config.yaml`).
- **Academic license**: Gurobi offers free academic licenses; see the *Academic Program and Licenses* page on the Gurobi website for activation instructions.
- **Alternative solvers**: other MILP solvers supported by PyPSA/linopy (e.g., HiGHS, CPLEX) can in principle be used by changing `solving.solver.name` and the corresponding `solver_options`. Large-scale runs may be slower or fail to converge, so Gurobi is recommended for exact reproduction of published results.

## Quick Start

1. **Edit** `config.yaml` — set planning horizons, scenario parameters, and solver options.

2. **Run the full pipeline**:
```bash
snakemake -j 1 solve_all_networks
```

3. **Generate summaries and plots**:
```bash
snakemake -j 1 plot_all
```

### Heat demand input (replace with your model output)

The heating demand time series used for the heating-sector coupling is read from the HDF5 file configured in the workflow (by default the heat-demand input includes e.g. `data/heating/heat_demand_profile_positive_2030.h5`).

The code expects a HDF5 key:
- Preferred: `/heat_demand_profiles`
- Fallback: if the preferred key is missing, the **first key** found in the file is used (and a warning is logged).

The dataset should be a table shaped like:
- index: timestamps aligned to `network.snapshots` (resolution controlled by `config.yaml: freq`, e.g. `6h`)
- columns: provinces (`pro_names`)
- values: heat demand as power (MW_th) used as `Load.p_set` on `"<province> central heat"` and `"<province> decentral heat"`.

### Building thermal inertia (demand-side heat storage)

Optional demand-side building inertia can be enabled by adding heat-storage `Store`s on **both** central and decentral heat buses.

- Parameter template: `data/heating/building_inertia_template.csv` (single file with separate central/decentral columns)
- Config switch in `config.yaml`:

```yaml
building_inertia:
  enabled: true
  params_csv: "data/heating/building_inertia_template.csv"
  carrier: "building thermal mass"
```

The CSV schema (columns):
- `province`
- `C_th_MWh_per_K_central`, `deltaT_K_central`, `standing_loss_per_hour_central`
- `C_th_MWh_per_K_decentral`, `deltaT_K_decentral`, `standing_loss_per_hour_decentral`

Effective storage energy is computed as: \(e\_nom = C\_{th}\,[\mathrm{MWh/K}] \times \Delta T\,[\mathrm{K}]\).

### Exogenous electricity prices at coarse time resolution (e.g. 6h)

When `config.yaml: freq` is coarser than 1 hour (e.g. `6h`, so 1460 snapshots), but the price CSV is hourly (e.g. `hour=1..8760`), the price loader will **automatically aggregate** hourly prices into slot blocks (default: mean) to match the network snapshots.

### Running with Aluminum Integration

Enable aluminum smelter co-optimization by setting the following in `config.yaml`:

```yaml
add_aluminum: True
aluminum_commitment: False          # keep False for iterative mode
aluminum_max_iterations: 10         # max power–aluminum iterations
aluminum_convergence_tolerance: 0.01
aluminum_capacity_ratio: 1.0        # 1.0 = 100 % of installed capacity
```

Then run the pipeline as above. The solver will automatically enter the iterative aluminum loop inside `solve_network_myopic`.

### Running on HPC with SLURM

```bash
python scripts/generate_slurm_jobs_advanced.py   # generate job files
./submit_multiple_jobs.sh                         # submit all scenarios
squeue -u $USER                                   # monitor
```

See the [SLURM Jobs Guide](docs/slurm_jobs_guide.md) for details.

## Configuration

All parameters live in `config.yaml`. Scenario-specific overrides are stored in `configs/` (over 1 000 pre-generated files covering the full scenario matrix).

### Core Switches

```yaml
add_aluminum: True                       # enable aluminum module
aluminum_commitment: False               # unit-commitment in main problem (keep False for iterative)
aluminum_max_iterations: 10
aluminum_convergence_tolerance: 0.01
aluminum_capacity_ratio: 1.0             # scale smelter capacity
```

### Wind/Solar Capacity Guards (current default)

To prevent unrealistic VRE expansion in myopic planning, the model applies pre-solve national-to-provincial guards for wind and solar:

- **Wind guard**: `wind_capacity_guard` in `config.yaml`, implemented in `scripts/wind_capacity_guard.py`
- **Solar guard**: `solar_capacity_guard` in `config.yaml`, implemented in `scripts/solar_capacity_guard.py`
- **Applied years**: `2025` to `2060`
- **Band constraints**: lower = `0.8 × target`, upper = `1.3 × target` (`allow_underbuild_only: false`)

Target files:

- Wind (`onwind`/`offwind`): `data/p_nom/national_wind_capacity_from_planning.csv`
- Solar: `data/p_nom/national_solar_capacity_from_external_targets.csv`

Solar target trajectory currently follows the CPIA 2026 outlook baseline path:

- 2026 additions range: 180–240 GW (baseline uses +180 GW)
- 2026–2030 average additions range: 238–287 GW/yr (baseline uses +238 GW/yr)
- Post-2030 planning years continue with +238 GW/yr unless replaced by newer official assumptions

These guards do **not** override physical provincial potential ceilings: `p_nom_max` potential limits from `prepare_base_network` remain active and still cap the final feasible upper bound.

### Scenario Dimensions

```yaml
aluminum:
  current_scenario:
    smelter_flexibility: "mid"           # low / mid / high
    primary_demand: "mid"                # low / mid / high
  scenario_dimensions:
    smelter_flexibility:
      low:  { p_min_pu: 0.99, restart_cost: 96594,  stand_by_cost: 1.2 }
      mid:  { p_min_pu: 0.9,  restart_cost: 13981,  stand_by_cost: 1.2 }
      high: { p_min_pu: 0.7,  restart_cost: 2796,   stand_by_cost: 1.2 }
      non_constrained: { p_min_pu: 0.0, restart_cost: 0, stand_by_cost: 0 }
```

### Solver Settings

```yaml
solving:
  solver:
    name: gurobi
  solver_options:
    default:
      Threads: 192
      Method: 2            # barrier
```

## Project Structure

```
PyPSA-China/
├── config.yaml                # main configuration
├── configs/                   # 1 000+ scenario-specific configs
├── Snakefile                  # Snakemake workflow
├── data/
│   ├── aluminum_demand/       # demand scenarios (JSON)
│   ├── p_nom/                 # smelter capacity by province (CSV)
│   ├── costs/                 # technology cost projections
│   ├── grids/                 # grid topology
│   ├── load/                  # provincial load profiles
├── resources/                 # renewable generation profiles
├── scripts/
│   ├── prepare_base_network*.py
│   ├── add_existing_baseyear.py
│   ├── add_brownfield.py
│   ├── solve_network_myopic.py
│   ├── scenario_utils.py
│   ├── plot_*.py
│   └── generate_slurm_jobs_advanced.py
├── docs/                      # documentation (see below)
├── envs/                      # conda environment files
├── results/                   # output networks, summaries, plots
└── LICENSES/
```

## Documentation

Detailed documentation is provided in the `docs/` folder:

| Document | Description |
|----------|-------------|
| [Aluminum Integration Guide](docs/aluminum_integration_guide.md) | End-to-end explanation of how aluminum demand data, smelter capacity, and model components (Link, Store, Load, Hub) are assembled, including unit-conversion formulas and the data-flow diagram. |
| [Iterative Optimization Notes](docs/README_aluminum_iterative.md) | Refactoring notes for the aluminum iterative algorithm: convergence criterion, network reload strategy, `p_set` fixing, virtual-generator marginal costs, and the potline-based representative-line method. |
| [Flexible Aluminum Smelting Intro](docs/flexible_aluminum_smelting_intro.md) | Technical feasibility report on flexible aluminum smelting — EnPot/TRIMET evidence, historical curtailment events, the economic logic of seasonal batch operation, and potline-level modeling parameters for China. |
| [Scenario Dimensions Guide](docs/scenario_dimensions_guide.md) | How to configure and use the three scenario dimensions (smelter flexibility, primary demand, grid-interaction market opportunity) and generate all 27 combinations. |
| [Scenario Visualization Guide](docs/scenario_visualization_guide.md) | Instructions for the maintained scenario post-processing scripts: value comparison, capacity/cost panels, and optimal-point plots. |
| [SLURM Jobs Guide](docs/slurm_jobs_guide.md) | Generating, submitting, monitoring, and troubleshooting SLURM batch jobs on HPC clusters. |
| [Price Module Report](docs/price_module_market_clearing_report.md) | Notes on the two-stage dispatch price workflow, mapped-price sidecar, and solar value-factor dataset. |
| [Solar Value Dataset Notes](docs/README_solar_value_dataset_2025.md) | How to fill and plot the 2025 solar value-factor dataset from dispatch price outputs. |

## Iterative Aluminum Optimization Algorithm

The aluminum module uses a price-based decomposition loop:

1. **Relaxed solve**: solve the full network with continuous (non-committable) aluminum links to obtain nodal marginal electricity prices.
2. **Aluminum sub-problem**: for each province in parallel, build a small MILP with a single representative potline (250 kt/yr, ~385 MW) scaled to the provincial total, using the nodal price as the virtual-generator marginal cost. Solve for optimal commitment and dispatch.
3. **Fix and re-solve**: write the resulting provincial aluminum time series back into the main network via `links_t.p_set` and `loads_t.p_set`, then re-solve.
4. **Convergence check**: stop when the relative change in the system objective falls below the threshold (default 1 %).

This approach keeps the main problem as a tractable LP while capturing potline-level start-up/shut-down economics in the sub-problem.

## Scenario Analysis and Visualization

After completing scenario runs, generate comparison figures with the maintained
post-processing scripts:

```bash
# cost/value changes across demand × market × flexibility scenarios
python scripts/plot_value_scenario_comparison.py

# publication-style 2050 MMM capacity/cost panel
python scripts/plot_capacity_MMM_2050.py

# optimal capacity points across years and market/flexibility settings
python scripts/plot_optimal_point.py
```

Outputs are saved under the configured `results/version-*` directories and script-specific
analysis folders.

## Output Files

Results are organized by version and scenario:

```
results/
└── version-<version>-<scenario>/
    ├── networks/                        # intermediate .nc files
    ├── postnetworks/                    # solved networks
    └── summary/
        └── postnetworks/
            └── costs.csv, capacities.csv
```

## Troubleshooting

| Problem | Suggestion |
|---------|------------|
| Solver license error | Verify Gurobi license with `gurobi_cl --license` |
| Out of memory | Increase `--mem-per-cpu` in SLURM or reduce network scope |
| Aluminum iteration does not converge | Raise `aluminum_convergence_tolerance` (e.g., 0.05) or increase `aluminum_max_iterations` |
| Missing data files | Check that all required CSVs and JSONs exist under `data/` |
| `ImportError: cannot import name 'Dict' from pypsa.descriptors` | Use the project environment in `envs/environment.yaml` (PyPSA ~0.29) or update `scripts/_helpers.py` to match your PyPSA version. |

## License

- **Code**: MIT License (`LICENSES/MIT.txt`)
- **Data**: CC0-1.0 (`LICENSES/CC0-1.0.txt`)
- **Documentation**: CC-BY-4.0 (`LICENSES/CC-BY-4.0.txt`)

## Citation

If you use PyPSA-China-aluminum in your research, please cite:

```bibtex
@article{lyu2025aluminum,
  title   = {Can industrial overcapacity enable seasonal flexibility in
             electricity use? {A} case study of aluminum smelting in {China}},
  author  = {Lyu, Ruike and Jenkins, Jesse D. and others},
  year    = {2025},
  journal = {arXiv preprint arXiv:2511.22839},
  url     = {https://arxiv.org/abs/2511.22839}
}
```

## Contributing

Contributions are welcome. Please fork the repository, create a feature branch, and submit a pull request.

## Acknowledgments

The codebase of PyPSA-China originates from the work of the [2022 PyPSA-China Authors](https://github.com/PyPSA/PyPSA-China) and builds on the [PyPSA](https://pypsa.org/) framework. Technology cost data and learning trajectories in the core (Mid) scenario are adopted from the [PyPSA-Eur](https://pypsa-eur.readthedocs.io/en/latest/) technology database and the [PyPSA-China-PIK](https://github.com/pik-piam/PyPSA-China-PIK) dataset for China-specific costs, both primarily based on the Danish Energy Agency technology catalogues. Low and High cost cases for flexibility-related technologies are constructed by scaling investment costs by −20 % and +50 % relative to the Mid case, consistent with the accuracy ranges recommended for Class 4 estimates in the AACE International Cost Estimate Classification System (Bates, 2005).

## Contact

For questions and support, please open an issue on GitHub or contact the maintainers.
