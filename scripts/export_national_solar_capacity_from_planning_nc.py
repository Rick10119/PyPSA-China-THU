#!/usr/bin/env python3
"""
Export national solar installed capacity by planning year from planning NC files.

This script reads config.yaml, locates solved planning postnetwork files, and
computes national total solar installed capacity for each planning horizon.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import pypsa
import yaml


def _load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return cfg


def _select_single(name: str, value):
    if isinstance(value, list):
        if not value:
            raise ValueError(f"Config field {name} list is empty.")
        return value[0]
    return value


def _postnetwork_path(
    repo_root: Path,
    results_dir: str,
    version: str,
    heating_demand: str,
    opts: str,
    topology: str,
    pathway: str,
    year: int,
) -> Path:
    return (
        repo_root
        / results_dir
        / f"version-{version}"
        / "postnetworks"
        / heating_demand
        / f"postnetwork-{opts}-{topology}-{pathway}-{year}.nc"
    )


def _solar_capacity_mw(n: pypsa.Network, carrier_pattern: str) -> float:
    carriers = n.generators.carrier.astype(str)
    mask = carriers.str.contains(carrier_pattern, case=False, regex=True)
    gens = n.generators.loc[mask]
    if gens.empty:
        return 0.0
    cap_col = "p_nom_opt" if "p_nom_opt" in gens.columns else "p_nom"
    return float(gens[cap_col].sum())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export national solar installed capacity from planning NC files."
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Override version from config (e.g. 0505.1H.2)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV path. Default: results/version-<version>/national_solar_capacity_from_planning_nc.csv",
    )
    parser.add_argument(
        "--carrier-pattern",
        default=r"^solar$",
        help="Regex for solar carriers in generators (default: ^solar$)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail if any planning year NC is missing.",
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    repo_root = config_path.parent
    cfg = _load_config(config_path)

    version = str(args.version if args.version is not None else cfg.get("version", "")).strip()
    if not version:
        raise ValueError("Version is empty. Set in config.yaml or pass --version.")

    results_dir = str(cfg.get("results_dir", "results/")).strip()
    scenario = cfg.get("scenario", {})
    planning_horizons = [int(y) for y in scenario.get("planning_horizons", [])]
    if not planning_horizons:
        raise ValueError("scenario.planning_horizons is empty in config.")

    opts = str(_select_single("scenario.opts", scenario.get("opts", [])))
    topology = str(_select_single("scenario.topology", scenario.get("topology", "")))
    pathway = str(_select_single("scenario.pathway", scenario.get("pathway", [])))
    heating_demand = str(_select_single("scenario.heating_demand", scenario.get("heating_demand", [])))

    if args.output:
        output_path = Path(args.output).resolve()
    else:
        output_path = (
            repo_root
            / results_dir
            / f"version-{version}"
            / "national_solar_capacity_from_planning_nc.csv"
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    missing: list[int] = []
    for year in planning_horizons:
        nc_path = _postnetwork_path(
            repo_root=repo_root,
            results_dir=results_dir,
            version=version,
            heating_demand=heating_demand,
            opts=opts,
            topology=topology,
            pathway=pathway,
            year=year,
        )
        if not nc_path.exists():
            missing.append(year)
            continue

        n = pypsa.Network(nc_path)
        solar_cap_mw = _solar_capacity_mw(n, args.carrier_pattern)
        rows.append(
            {
                "year": year,
                "national_solar_capacity_mw": solar_cap_mw,
                "national_solar_capacity_gw": solar_cap_mw / 1000.0,
                "network_path": str(nc_path),
            }
        )

    if missing and args.strict:
        raise FileNotFoundError(f"Missing planning NC for years: {missing}")

    df = pd.DataFrame(rows).sort_values("year")
    df.to_csv(output_path, index=False)

    print(f"Version: {version}")
    print(f"Processed years: {df['year'].tolist() if not df.empty else []}")
    print(f"Missing years: {missing}")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
