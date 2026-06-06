# SPDX-FileCopyrightText: 2026 Ruike Lyu
#
# SPDX-License-Identifier: MIT
"""Build a PyPSA solve model and export a Gurobi IIS report.

Run this from the repository root with the same Windows user that owns the
Gurobi license, for example:

    C:/ProgramData/Anaconda3/envs/pypsa/python.exe scripts/diagnose_gurobi_iis.py --year 2030
    C:/ProgramData/Anaconda3/envs/pypsa/python.exe scripts/diagnose_gurobi_iis.py --mode dispatch --year 2060

The script does not run the full Snakemake workflow. It reads the existing
prenetwork-brownfield input, builds the same main network optimization model,
and asks linopy/Gurobi to format the infeasible constraint set when the model is
infeasible.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pypsa
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from _helpers import configure_logging, override_component_attrs  # noqa: E402
import solve_network_myopic as snm  # noqa: E402
import run_dispatch_segmented_prices as dseg  # noqa: E402


class Wildcards(SimpleNamespace):
    """Small snakemake-like object for code paths that call ``wildcards.keys()``."""

    def keys(self):
        return self.__dict__.keys()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a Gurobi IIS report for a PyPSA main solve.")
    parser.add_argument(
        "--mode",
        choices=("main", "dispatch"),
        default="main",
        help="Model to diagnose: main expansion solve or dispatch-segmented re-dispatch.",
    )
    parser.add_argument("--config", default="config.yaml", help="Config YAML path.")
    parser.add_argument("--year", default="2030", help="Planning horizon to diagnose.")
    parser.add_argument("--opts", default="ll", help="Scenario opts wildcard.")
    parser.add_argument("--topology", default=None, help="Topology wildcard; defaults to config scenario.topology.")
    parser.add_argument("--pathway", default=None, help="Pathway wildcard; defaults to first config scenario.pathway.")
    parser.add_argument("--heating-demand", default="positive", help="Heating-demand wildcard.")
    parser.add_argument("--network", default=None, help="Optional explicit input network path.")
    parser.add_argument("--overrides", default="data/override_component_attrs", help="Override component attrs path.")
    parser.add_argument("--out", default=None, help="Optional IIS report output path.")
    parser.add_argument(
        "--display-max-terms",
        type=int,
        default=12,
        help="Maximum terms per infeasible constraint in the formatted report.",
    )
    return parser.parse_args()


def load_config(path: str) -> dict:
    with open(ROOT / path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def default_network_path(cfg: dict, args: argparse.Namespace) -> Path:
    results_dir = cfg.get("results_dir", "results/")
    version = cfg["version"]
    topology = args.topology or cfg["scenario"]["topology"]
    pathway = args.pathway or cfg["scenario"]["pathway"][0]
    return (
        ROOT
        / results_dir
        / f"version-{version}"
        / "prenetworks-brownfield"
        / args.heating_demand
        / f"prenetwork-{args.opts}-{topology}-{pathway}-{args.year}.nc"
    )


def default_dispatch_network_path(cfg: dict, args: argparse.Namespace) -> Path:
    results_dir = cfg.get("results_dir", "results/")
    version = cfg["version"]
    topology = args.topology or cfg["scenario"]["topology"]
    pathway = args.pathway or cfg["scenario"]["pathway"][0]
    return (
        ROOT
        / results_dir
        / f"version-{version}"
        / "postnetworks"
        / args.heating_demand
        / f"postnetwork-{args.opts}-{topology}-{pathway}-{args.year}.nc"
    )


def install_fake_snakemake(cfg: dict, args: argparse.Namespace, network_path: Path) -> None:
    topology = args.topology or cfg["scenario"]["topology"]
    pathway = args.pathway or cfg["scenario"]["pathway"][0]
    wildcards = Wildcards(
        opts=args.opts,
        topology=topology,
        pathway=pathway,
        planning_horizons=str(args.year),
        heating_demand=args.heating_demand,
    )
    snm.snakemake = SimpleNamespace(
        config=cfg,
        wildcards=wildcards,
        input=SimpleNamespace(
            network=str(network_path),
            overrides=args.overrides,
            al_smelter_p_max=str(ROOT / "data" / "p_nom" / "al_smelter_p_max.csv"),
        ),
        params=SimpleNamespace(
            solving=cfg["solving"],
            using_single_node=cfg.get("using_single_node", False),
            single_node_province=cfg.get("single_node_province", "Shandong"),
            iterative_optimization=cfg.get("iterative_optimization", False),
        ),
        log=SimpleNamespace(solver=""),
    )


def prepare_dispatch_network(n: pypsa.Network, cfg: dict, args: argparse.Namespace) -> pypsa.Network:
    solve_opts = cfg.get("solving", {}).get("options", {})
    n = snm.prepare_network(
        n,
        solve_opts,
        using_single_node=cfg.get("using_single_node", False),
        single_node_province=cfg.get("single_node_province", "Shandong"),
        config=cfg,
        planning_horizon=str(args.year),
    )

    if solve_opts.get("nhours"):
        nh = int(solve_opts["nhours"])
        if hasattr(n, "global_constraints") and not n.global_constraints.empty:
            scale = float(nh) / 8760.0
            if "constant" in n.global_constraints.columns:
                n.global_constraints["constant"] = n.global_constraints["constant"].astype(float) * scale
        try:
            n.snapshot_weightings[:] = 1.0
        except Exception:
            pass

    dseg.freeze_capacities_and_zero_capex(n)

    dispatch_cfg = cfg.get("dispatch_segmented_prices") or {}
    carriers_cfg = dispatch_cfg.get("carriers") or cfg.get("dispatch_segmented_carriers") or {}
    if dispatch_cfg.get("first_segment_from_fuel_cost", True) and carriers_cfg:
        try:
            yref = int(args.year)
            cost_fn = ROOT / "data" / "costs" / f"costs_{yref}.csv"
            if cost_fn.is_file():
                costs_tbl = dseg.load_costs(
                    str(cost_fn),
                    cfg["costs"],
                    cfg["electricity"],
                    float(yref),
                    dseg._snapshot_n_years(n),
                )
                costs_tbl = dseg.apply_market_scenario_costs(costs_tbl, cfg)
                dseg.patch_first_segment_marginal_from_fuel_cost(
                    carriers_cfg,
                    costs_tbl,
                    dispatch_cfg=dispatch_cfg,
                )
        except Exception as e:
            logging.getLogger(__name__).warning("first_segment_from_fuel_cost failed (%s); using config marginal_cost.", e)

    dseg.apply_segmented_carriers(n, carriers_cfg)
    if dispatch_cfg.get("zero_gas_fuel_marginal_cost", True) and "OCGT gas" in (carriers_cfg.get("Link") or {}):
        dseg.zero_gas_fuel_marginal_cost(n)

    return n


def solve_model(n: pypsa.Network, cfg: dict, args: argparse.Namespace) -> tuple[str, str]:
    n.config = cfg
    n.opts = [o for o in str(args.opts).split("-") if o]

    solving = cfg["solving"]
    option_set = solving["solver"]["options"]
    solver_options = dict(solving["solver_options"][option_set]) if option_set else {}
    solver_options["DualReductions"] = 0
    solver_name = solving["solver"]["name"]

    solve_opts = cfg.get("solving", {}).get("options", {})
    skip_iterations = solve_opts.get("skip_iterations", False)
    if hasattr(n, "lines") and (n.lines.empty or not n.lines.s_nom_extendable.any()):
        skip_iterations = True

    extra_functionality = dseg.extra_functionality_dispatch if args.mode == "dispatch" else snm.extra_functionality
    if skip_iterations:
        return n.optimize(
            solver_name=solver_name,
            solver_options=solver_options,
            extra_functionality=extra_functionality,
        )

    return n.optimize.optimize_transmission_expansion_iteratively(
        solver_name=solver_name,
        solver_options=solver_options,
        track_iterations=solve_opts.get("track_iterations", False),
        min_iterations=solve_opts.get("min_iterations", 4),
        max_iterations=solve_opts.get("max_iterations", 6),
        extra_functionality=extra_functionality,
    )


def main() -> int:
    os.chdir(ROOT)
    args = parse_args()
    cfg = load_config(args.config)
    if args.network:
        network_path = Path(args.network)
    elif args.mode == "dispatch":
        network_path = default_dispatch_network_path(cfg, args)
    else:
        network_path = default_network_path(cfg, args)
    network_path = network_path if network_path.is_absolute() else ROOT / network_path
    if not network_path.is_file():
        raise FileNotFoundError(f"Input network not found: {network_path}")

    out = Path(args.out) if args.out else ROOT / "diagnostics" / f"gurobi_iis_{args.mode}_{args.year}.txt"
    out = out if out.is_absolute() else ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(level=logging.INFO)
    install_fake_snakemake(cfg, args, network_path)

    overrides = override_component_attrs(args.overrides) if args.overrides else None
    n = pypsa.Network(str(network_path), override_component_attrs=overrides)
    n._network_path = str(network_path)
    if args.overrides:
        n._overrides_path = args.overrides

    solve_opts = cfg.get("solving", {}).get("options", {})
    if args.mode == "dispatch":
        n = prepare_dispatch_network(n, cfg, args)
    else:
        n = snm.prepare_network(
            n,
            solve_opts,
            using_single_node=cfg.get("using_single_node", False),
            single_node_province=cfg.get("single_node_province", "Shandong"),
            config=cfg,
            planning_horizon=str(args.year),
        )

    status, condition = solve_model(n, cfg, args)

    lines = [
        f"mode: {args.mode}",
        f"network: {network_path}",
        f"solver: {cfg['solving']['solver']['name']}",
        f"status: {status}",
        f"condition: {condition}",
        "",
    ]

    if str(condition) not in {"infeasible", "infeasible_or_unbounded"}:
        lines.append("Model was not reported infeasible; IIS was not requested.")
    else:
        lines.append("IIS / infeasible constraints:")
        lines.append("")
        lines.append(n.model.format_infeasibilities(display_max_terms=args.display_max_terms))

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote IIS report: {out}")
    print(f"status={status} condition={condition}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
