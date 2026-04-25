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
│   └── resources/             # renewable resource data
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
| [Flexible Aluminum Smelting Intro](docs/Flexible%20Aluminum%20Smelting%20Intro.md) | Technical feasibility report on flexible aluminum smelting — EnPot/TRIMET evidence, historical curtailment events, the economic logic of seasonal batch operation, and potline-level modeling parameters for China. |
| [Scenario Dimensions Guide](docs/scenario_dimensions_guide.md) | How to configure and use the three scenario dimensions (smelter flexibility, primary demand, grid-interaction market opportunity) and generate all 27 combinations. |
| [Scenario Visualization Guide](docs/scenario_visualization_guide.md) | Instructions for `plot_scenario_comparison.py` — 9-panel comparison charts, summary tables, cost categorization, and CLI usage. |
| [SLURM Jobs Guide](docs/slurm_jobs_guide.md) | Generating, submitting, monitoring, and troubleshooting SLURM batch jobs on HPC clusters. |

## Iterative Aluminum Optimization Algorithm

The aluminum module uses a price-based decomposition loop:

1. **Relaxed solve**: solve the full network with continuous (non-committable) aluminum links to obtain nodal marginal electricity prices.
2. **Aluminum sub-problem**: for each province in parallel, build a small MILP with a single representative potline (250 kt/yr, ~385 MW) scaled to the provincial total, using the nodal price as the virtual-generator marginal cost. Solve for optimal commitment and dispatch.
3. **Fix and re-solve**: write the resulting provincial aluminum time series back into the main network via `links_t.p_set` and `loads_t.p_set`, then re-solve.
4. **Convergence check**: stop when the relative change in the system objective falls below the threshold (default 1 %).

This approach keeps the main problem as a tractable LP while capturing potline-level start-up/shut-down economics in the sub-problem.

## Scenario Analysis and Visualization

After completing scenario runs, generate comparison figures:

```bash
# cost changes across all demand × market × flexibility scenarios
python scripts/plot_scenario_comparison.py --file-type costs --verbose

# capacity changes
python scripts/plot_scenario_comparison.py --file-type capacities --verbose
```

Output is saved to `results/scenario_analysis/` and includes 9-panel bar charts and CSV summary tables.

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
