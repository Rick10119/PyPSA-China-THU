<!-- SPDX-FileCopyrightText: 2026 Ruike Lyu -->
<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Scenario Result Visualization Guide

This guide points to the maintained post-processing scripts in the current
`price-simulation` workflow. Older documentation referenced
`scripts/plot_scenario_comparison.py`; that script is no longer part of the
repository.

## Maintained Scripts

### `scripts/plot_value_scenario_comparison.py`

Compares value/cost changes across flexibility, demand, and market-opportunity
scenarios. It reads scenario summaries from `results/version-*` folders and
builds multi-panel comparison figures and CSV outputs.

Typical use:

```bash
python scripts/plot_value_scenario_comparison.py
```

Use `scripts/plot_value_scenario_comparison_f.py` for the favorable-employment
variant when the corresponding scenario outputs are available.

### `scripts/plot_capacity_MMM_2050.py`

Builds the publication-style 2050 capacity/cost panel for the MMM scenario
family.

Typical use:

```bash
python scripts/plot_capacity_MMM_2050.py
```

### `scripts/plot_optimal_point.py`

Finds and plots optimal aluminum capacity points across planning years,
market-opportunity levels, and flexibility settings.

Typical use:

```bash
python scripts/plot_optimal_point.py
```

### Price and Solar-Value Diagnostics

For price-module diagnostics and solar value-factor analysis, use:

```bash
python scripts/plot_shandong_price_vs_thermal.py
python scripts/plot_solar_value_factor_yearly.py
python scripts/fill_solar_value_dataset_2025.py
```

See also:

- `docs/price_module_market_clearing_report.md`
- `docs/README_solar_value_dataset_2025.md`

## Expected Inputs

The scripts expect solved scenario outputs under versioned result folders, for
example:

```text
results/
└── version-<version>-<scenario>/
    └── summary/
        └── postnetworks/
            └── <heating_demand>/
                └── postnetwork-<opts>-<topology>-<pathway>-<year>/
                    ├── costs.csv
                    └── capacities.csv
```

Exact file names depend on the script and the configured scenario suffixes.
When a script cannot find an input, it logs the missing path so the run can be
matched against the relevant `config_<scenario>.yaml` file.

## Recommended Publication Workflow

1. Run or verify all required scenario configurations.
2. Generate summary CSVs with the Snakemake `plot_all` target or the relevant
   summary rules.
3. Run the maintained plotting script for the target figure family.
4. Check generated CSV sidecars before using the figure in the manuscript.

## Notes

- The scenario code convention is generally
  `<flexibility><demand><market><employment>`, for example `MMMF`.
- Capacity-ratio scenarios use suffixes such as `5p`, `15p`, `100p`,
  `non_flexible`, and `no_aluminum`.
- For large scenario matrices, generate job files with
  `scripts/generate_slurm_jobs_advanced.py` and submit them with
  `submit_multiple_jobs.sh`.
