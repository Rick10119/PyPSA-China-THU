# SPDX-FileCopyrightText: : 2026
# SPDX-License-Identifier: MIT

import os

# Avoid slow/hanging font cache builds on machines where default cache
# directories are not writable (common in restricted environments).
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
os.environ.setdefault("MPLCONFIGDIR", os.path.join(_repo_root, ".cache", "matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(_repo_root, ".cache"))

import logging

import numpy as np
import pandas as pd
import pypsa

from pathlib import Path

try:
    # Works with the project's original PyPSA (e.g. 0.29) environment.
    from _helpers import configure_logging, override_component_attrs  # type: ignore
except Exception:  # pragma: no cover
    configure_logging = None
    override_component_attrs = None

from heat_decoupled_utils import (
    add_exogenous_price_settlement,
    strip_power_system_keep_heat,
)
from load_electricity_prices import ElectricityPriceSpec, load_electricity_prices
from solve_network_myopic import ALLOWED_OPTIMIZE_KWARGS, add_chp_constraints

logger = logging.getLogger(__name__)

def configure_logging_from_config(config: dict, *, rule: str | None = None, logfile: str | None = None) -> None:
    kwargs = (config.get("logging") or {}) if isinstance(config, dict) else {}
    kwargs = dict(kwargs) if isinstance(kwargs, dict) else {}
    kwargs.setdefault("level", "INFO")
    kwargs.setdefault("format", "%(levelname)s:%(name)s:%(message)s")

    handlers = []
    if logfile:
        Path(logfile).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(logfile))
    handlers.append(logging.StreamHandler())
    kwargs["handlers"] = handlers

    logging.basicConfig(**kwargs)


def _compute_co2_emissions_from_solution(n: pypsa.Network) -> float:
    """
    Compute total CO2 emissions based on solved dispatch and carrier emission factors.

    Units follow the model convention: sum_t w_t * energy * carriers.co2_emissions.
    """
    if "co2_emissions" not in n.carriers.columns:
        return 0.0

    weights_h = n.snapshot_weightings.generators
    ce = n.carriers.co2_emissions.fillna(0.0)

    total = 0.0

    if hasattr(n, "generators") and not n.generators.empty and hasattr(n.generators_t, "p") and not n.generators_t.p.empty:
        g = n.generators.copy()
        g["co2_emissions"] = g.carrier.map(ce).fillna(0.0)
        g = g[g.co2_emissions != 0]
        if not g.empty:
            energy = n.generators_t.p[g.index].mul(weights_h, axis=0).sum()  # MWh_el
            total += float((energy * g.co2_emissions).sum())

    if hasattr(n, "links") and not n.links.empty and hasattr(n.links_t, "p0") and not n.links_t.p0.empty:
        l = n.links.copy()
        l["co2_emissions"] = l.carrier.map(ce).fillna(0.0)
        l = l[l.co2_emissions != 0]
        if not l.empty:
            # p0 is input at bus0 (fuel/primary energy for thermal links)
            energy = n.links_t.p0[l.index].mul(weights_h, axis=0).sum()  # MWh_primary
            total += float((energy * l.co2_emissions).sum())

    return total


def _apply_heatonly_co2_limit(n: pypsa.Network, config: dict) -> None:
    """
    Optionally tighten the CO2 constraint based on a baseline network.

    Supported config:
      heatonly_co2_limit:
        enabled: true|false
        relative_to_baseline: 0.5
        baseline_network: "path/to/heatonly_postnetwork-*.nc"  # must exist
        name: "co2_limit"  # optional
    """
    co2_cfg = (config.get("heatonly_co2_limit") or {}) if isinstance(config, dict) else {}
    if not co2_cfg or not bool(co2_cfg.get("enabled", False)):
        return

    name = str(co2_cfg.get("name", "co2_limit"))
    rel = float(co2_cfg.get("relative_to_baseline", 0.5))
    baseline_path = co2_cfg.get("baseline_network")
    if not baseline_path:
        raise ValueError("heatonly_co2_limit.enabled=true requires heatonly_co2_limit.baseline_network")

    baseline_n = pypsa.Network(baseline_path)
    baseline_emissions = _compute_co2_emissions_from_solution(baseline_n)
    limit = rel * baseline_emissions

    if name not in n.global_constraints.index:
        n.add(
            "GlobalConstraint",
            name,
            type="primary_energy",
            carrier_attribute="co2_emissions",
            sense="<=",
            constant=limit,
        )
    else:
        n.global_constraints.at[name, "type"] = "primary_energy"
        n.global_constraints.at[name, "carrier_attribute"] = "co2_emissions"
        n.global_constraints.at[name, "sense"] = "<="
        n.global_constraints.at[name, "constant"] = limit

    logger.info(
        "Applied heat-only CO2 limit: %s.constant=%.6g (relative %.3f of baseline %.6g from %s)",
        name,
        limit,
        rel,
        baseline_emissions,
        baseline_path,
    )

def _hardcode_co2_limit_from_config(n: pypsa.Network, *, config: dict, pathway: str, year: int) -> None:
    """
    Hard-coded CO2 cap from `config.yaml` reduction schedule.

    Uses:
      config['scenario']['co2_reduction'][pathway][str(year)] = r
    Interpreted as: reduction fraction r (e.g. 0.2 means -20% emissions),
    so cap = (1 - r) * baseline.

    Baseline emissions are currently hard-coded from the previously solved heat-only run:
      baseline_total_co2 = 77669849.38063258
    """
    baseline = 77669849.38063258

    r = 0.0
    try:
        r = float(((config.get("scenario") or {}).get("co2_reduction") or {}).get(pathway, {}).get(str(year), 0.0))
    except Exception:
        r = 0.0
    r = max(0.0, min(1.0, r))
    cap = (1.0 - r) * baseline

    name = "co2_limit"
    if name not in n.global_constraints.index:
        n.add(
            "GlobalConstraint",
            name,
            type="primary_energy",
            carrier_attribute="co2_emissions",
            sense="<=",
            constant=cap,
        )
    else:
        n.global_constraints.at[name, "type"] = "primary_energy"
        n.global_constraints.at[name, "carrier_attribute"] = "co2_emissions"
        n.global_constraints.at[name, "sense"] = "<="
        n.global_constraints.at[name, "constant"] = cap
    logger.info(
        "Hard-coded CO2 cap applied from config: pathway=%s year=%s r=%s -> %s.constant=%s",
        pathway,
        year,
        r,
        name,
        cap,
    )


def extra_functionality_heat_only(n: pypsa.Network, snapshots: pd.Index) -> None:
    # Keep CHP constraints; do not add transmission/retrofit constraints in heat-only mode.
    add_chp_constraints(n)


def solve_heat_decoupled(
    n: pypsa.Network,
    *,
    config: dict,
    solving: dict,
    opts: list[str],
    pathway: str,
    year: int,
) -> pypsa.Network:
    """
    Heat-only solve with exogenous electricity prices.
    """

    set_of_options = solving["solver"]["options"]
    solver_name = solving["solver"]["name"]
    solver_options = solving["solver_options"][set_of_options] if set_of_options else {}

    # Strip the electricity system while keeping heat + CHP + supporting fuels
    strip_power_system_keep_heat(n)

    # Load electricity prices and add settlement layer
    price_spec = ElectricityPriceSpec(
        region_col=config.get("electricity_prices", {}).get("region_col", "province"),
        bus_col=config.get("electricity_prices", {}).get("bus_col", "bus"),
        hour_col=config.get("electricity_prices", {}).get("hour_col", "hour"),
        snapshot_col=config.get("electricity_prices", {}).get("snapshot_col", "snapshot"),
        price_col=config.get("electricity_prices", {}).get("price_col", "price"),
        sheet_name=config.get("electricity_prices", {}).get("sheet_name"),
        timezone=config.get("electricity_prices", {}).get("timezone"),
    )

    electricity_prices = load_electricity_prices(
        snapshots=n.snapshots,
        path=config.get("electricity_prices", {}).get("path", "data/electricity_prices/electricity_prices.csv"),
        spec=price_spec,
        index_mode=config.get("electricity_prices", {}).get("index_mode", "auto"),
        region_mode=config.get("electricity_prices", {}).get("region_mode", "auto"),
        expected_regions=list(map(str, n.buses.index[n.buses.carrier == "AC"])),
        require_complete_hours=True,
        require_non_negative=config.get("electricity_prices", {}).get("require_non_negative", True),
        fallback_network_path=config.get("electricity_prices", {}).get("fallback_network_path"),
        export_if_missing=True,
        export_format=config.get("electricity_prices", {}).get("export_format", "long"),
        export_split_per_region=bool(config.get("electricity_prices", {}).get("export_split_per_region", True)),
        export_write_combined=True,
    )

    add_exogenous_price_settlement(
        n,
        electricity_prices,
        import_carrier=config.get("electricity_prices", {}).get("import_carrier", "grid import"),
        export_carrier=config.get("electricity_prices", {}).get("export_carrier", "grid export"),
        p_nom=float(config.get("electricity_prices", {}).get("p_nom", 1e9)),
    )

    # User request: hard-code CO2 constraint driven by config.yaml co2_reduction
    _hardcode_co2_limit_from_config(n, config=config, pathway=pathway, year=year)

    # Attach for extra_functionality compatibility
    n.config = config
    n.opts = opts

    # Heat-only solve uses the plain `n.optimize(...)` call.
    # Do NOT forward generic `solving.options` keys (like track_iterations/min_iterations/max_iterations),
    # since those are meant for transmission-expansion iterative routines and will be interpreted as
    # solver parameters (e.g. by gurobi) and fail.
    optimize_kwargs = {}

    status, condition = n.optimize(
        solver_name=solver_name,
        solver_options=solver_options,
        extra_functionality=extra_functionality_heat_only,
        **optimize_kwargs,
    )
    logger.info("Heat-decoupled solve finished: status=%s condition=%s", status, condition)

    # Store objective (compat)
    if hasattr(n, "model") and hasattr(n.model, "objective_value"):
        n.objective = n.model.objective_value

    return n


if __name__ == "__main__":
    # Two modes:
    # 1) Snakemake mode: snakemake injects a global `snakemake` object.
    # 2) Direct CLI mode: no snakemake dependency (recommended for quick runs).
    if "snakemake" in globals():
        if configure_logging is not None:
            configure_logging(snakemake)
        else:
            # Fallback for environments where `_helpers.py` is unavailable/incompatible
            logfile = None
            try:
                if hasattr(snakemake, "log") and snakemake.log:
                    logfile = (
                        snakemake.log.get("python", snakemake.log[0])
                        if hasattr(snakemake.log, "get")
                        else snakemake.log[0]
                    )
            except Exception:
                logfile = None
            configure_logging_from_config(
                snakemake.config,
                rule=getattr(snakemake, "rule", None),
                logfile=str(logfile) if logfile else None,
            )

        opts = snakemake.wildcards.opts
        if "sector_opts" in snakemake.wildcards.keys():
            opts += "-" + snakemake.wildcards.sector_opts
        opts = [o for o in opts.split("-") if o != ""]

        solve_opts = snakemake.params.solving["options"]
        np.random.seed(solve_opts.get("seed", 123))

        if override_component_attrs is not None and "overrides" in snakemake.input.keys():
            overrides = override_component_attrs(snakemake.input.overrides)
            n = pypsa.Network(snakemake.input.network, override_component_attrs=overrides)
        else:
            n = pypsa.Network(snakemake.input.network)

        from solve_network_myopic import prepare_network as prepare_network_for_solve

        n = prepare_network_for_solve(
            n,
            solve_opts,
            using_single_node=snakemake.params.using_single_node,
            single_node_province=snakemake.params.single_node_province,
        )

        n = solve_heat_decoupled(
            n,
            config=snakemake.config,
            solving=snakemake.params.solving,
            opts=opts,
            pathway=str(snakemake.wildcards.pathway),
            year=int(snakemake.wildcards.planning_horizons),
        )

        output_dir = os.path.dirname(snakemake.output.network_name)
        os.makedirs(output_dir, exist_ok=True)
        n.export_to_netcdf(snakemake.output.network_name)
    else:
        import argparse
        import logging
        from pathlib import Path

        import yaml

        parser = argparse.ArgumentParser(description="Solve heat-decoupled model without snakemake.")
        parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
        parser.add_argument("--network", default=None, help="Input network netcdf (e.g. prenetwork-*.nc)")
        parser.add_argument("--overrides", default="data/override_component_attrs", help="Override component attrs dir")
        parser.add_argument("--out", default=None, help="Output network netcdf path")
        parser.add_argument("--opts", default=None, help="Scenario opts string (e.g. ll)")
        parser.add_argument("--using-single-node", default=None, action="store_true", help="Keep only one province")
        parser.add_argument("--single-node-province", default=None, help="Province to keep in single-node mode")
        parser.add_argument("--nhours", type=int, default=None, help="Optional: clip to first N hours")
        args = parser.parse_args()

        with open(args.config, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # Minimal logging config without snakemake object
        log_cfg = (config.get("logging") or {}) if isinstance(config, dict) else {}
        logging.basicConfig(
            level=log_cfg.get("level", "INFO"),
            format=log_cfg.get("format", "%(levelname)s:%(name)s:%(message)s"),
        )

        run_cfg = (config.get("heat_decoupled_run") or {}) if isinstance(config, dict) else {}
        network_path = args.network or run_cfg.get("network")
        out_path = args.out or run_cfg.get("out")
        opts_str = args.opts or run_cfg.get("opts") or "ll"

        if not network_path or not out_path:
            raise SystemExit(
                "Direct run requires `heat_decoupled_run.network` and `heat_decoupled_run.out` in config.yaml "
                "(or pass --network/--out)."
            )

        if override_component_attrs is not None and args.overrides:
            overrides = override_component_attrs(args.overrides)
            n = pypsa.Network(network_path, override_component_attrs=overrides)
        else:
            n = pypsa.Network(network_path)

        solve_opts = dict(config.get("solving", {}).get("options", {}))
        nhours = args.nhours if args.nhours is not None else run_cfg.get("nhours")
        if nhours is not None:
            solve_opts["nhours"] = int(nhours)

        from solve_network_myopic import prepare_network as prepare_network_for_solve

        using_single_node = (
            bool(args.using_single_node)
            if args.using_single_node is not None
            else bool(config.get("using_single_node", False))
        )
        single_node_province = (
            args.single_node_province
            if args.single_node_province is not None
            else str(config.get("single_node_province", "Tianjin"))
        )

        n = prepare_network_for_solve(
            n,
            solve_opts,
            using_single_node=using_single_node,
            single_node_province=single_node_province,
        )

        opts = [o for o in opts_str.split("-") if o]

        n = solve_heat_decoupled(
            n,
            config=config,
            solving=config["solving"],
            opts=opts,
            pathway=str(run_cfg.get("pathway") or config.get("scenario", {}).get("pathway", ["linear2050"])[0]),
            year=int(run_cfg.get("planning_horizons") or config.get("scenario", {}).get("planning_horizons", [2030])[0]),
        )

        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        n.export_to_netcdf(out_path)

