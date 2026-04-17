#!/usr/bin/env python3
# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
"""
Generate config files for value tests and capacity tests.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List

import yaml


def clean_configs_dir() -> None:
    configs_dir = Path("configs")
    if configs_dir.exists():
        for file_path in configs_dir.glob("*"):
            if file_path.is_file():
                file_path.unlink()
                print(f"Removed: {file_path}")
        print("configs folder cleared")
    else:
        configs_dir.mkdir(exist_ok=True)
        print("Created configs folder")


def _load_base_config() -> dict:
    with open("config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _write_config(path: Path, config: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def run_value_tests(years: Iterable[int], clean: bool = True) -> None:
    if clean:
        print("=== Clearing configs folder ===")
        clean_configs_dir()
        print()

    base_config = _load_base_config()
    base_version = base_config["version"]
    configs_dir = Path("configs")
    configs_dir.mkdir(exist_ok=True)

    flexibility_levels = ["low", "mid", "high", "non_constrained"]
    demand_levels = ["low", "mid", "high"]
    market_levels = ["low", "mid", "high"]
    employment_levels = ["unfavorable", "favorable"]

    flex_map = {"low": "L", "mid": "M", "high": "H", "non_constrained": "N"}
    demand_map = {"low": "L", "mid": "M", "high": "H"}
    market_map = {"low": "L", "mid": "M", "high": "H"}
    employment_map = {"unfavorable": "U", "favorable": "F"}

    config_count = 0

    for year in years:
        for flex in flexibility_levels:
            for demand in demand_levels:
                for market in market_levels:
                    for employment in employment_levels:
                        scenario_suffix = (
                            f"{flex_map[flex]}{demand_map[demand]}{market_map[market]}{employment_map[employment]}"
                        )
                        new_config = base_config.copy()
                        new_config["version"] = f"{base_version}-{scenario_suffix}-{year}-100p"
                        new_config["add_aluminum"] = True
                        new_config["only_other_load"] = True
                        new_config["aluminum_capacity_ratio"] = 1.0
                        if flex == "non_constrained":
                            new_config["iterative_optimization"] = False
                            new_config["aluminum_commitment"] = False
                        new_config.setdefault("aluminum", {})
                        new_config["aluminum"]["capacity_ratio"] = 1.0
                        new_config["aluminum"].setdefault("current_scenario", {})
                        new_config["aluminum"]["current_scenario"].update(
                            {
                                "smelter_flexibility": flex,
                                "primary_demand": demand,
                                "market_opportunity": market,
                                "employment_transfer": employment,
                            }
                        )
                        new_config["costs"]["year"] = year
                        if "scenario" in new_config:
                            new_config["scenario"]["planning_horizons"] = [year]

                        config_path = configs_dir / f"config_{scenario_suffix}_{year}_100p.yaml"
                        _write_config(config_path, new_config)

                        config_count += 1
                        print(f"Generated config file {config_count}:")
                        print(f"  {scenario_suffix}_{year}_100p.yaml - version: {new_config['version']}")
                        print()

        print("=== Generating non-flexible config files ===")
        print("Note: For non-flexible scenarios, one config per market (fixed flex/demand/employment)")
        print()

        for market in market_levels:
            flex = "mid"
            demand = "mid"
            employment = "unfavorable"
            scenario_suffix = (
                f"{flex_map[flex]}{demand_map[demand]}{market_map[market]}{employment_map[employment]}"
            )

            new_config = base_config.copy()
            new_config["version"] = f"{base_version}-{scenario_suffix}-{year}-non_flexible"
            new_config["add_aluminum"] = False
            new_config["only_other_load"] = False
            new_config.setdefault("aluminum", {})
            new_config["aluminum"].setdefault("current_scenario", {})
            new_config["aluminum"]["current_scenario"].update(
                {
                    "smelter_flexibility": flex,
                    "primary_demand": demand,
                    "market_opportunity": market,
                    "employment_transfer": employment,
                }
            )
            new_config["aluminum"]["capacity_ratio"] = 1.0
            new_config["costs"]["year"] = year
            if "scenario" in new_config:
                new_config["scenario"]["planning_horizons"] = [year]

            config_path = configs_dir / f"config_{scenario_suffix}_{year}_non_flexible.yaml"
            _write_config(config_path, new_config)

            config_count += 1
            print(f"Generated non-flexible config file {config_count}:")
            print(f"  {scenario_suffix}_{year}_non_flexible.yaml - version: {new_config['version']}")
            print(f"  (Uses mid flex and demand, applies to all non-flexible scenarios)")
            print()

    print(f"Generated {config_count} config file(s) in total")


def _calculate_actual_capacity_ratio(year: int, cap_ratio: float, demand_level: str) -> float:
    # National primary-aluminum capacity / demand scenario (10k t/y), same series as
    # scripts/plot_optimal_point.calculate_actual_capacity_ratio; not read from config here.
    total_capacity = 4500
    demand_by_year = {
        "2030": 2902.417177819193,
        "2040": 1508.1703393209764,
        "2050": 1166.6836345743664,
    }
    demand = demand_by_year.get(str(year), 2902.417177819193)
    actual_ratio = (demand / total_capacity) * (1 - cap_ratio) + cap_ratio
    return actual_ratio


def run_capacity_tests(years: Iterable[int], clean: bool = True) -> None:
    if clean:
        print("=== Clearing configs folder ===")
        clean_configs_dir()
        print()

    base_config = _load_base_config()
    base_version = base_config["version"]
    configs_dir = Path("configs")
    configs_dir.mkdir(exist_ok=True)

    flexibility_levels = ["low", "mid", "high", "non_constrained"]
    demand_level = "mid"
    market_levels = ["low", "mid", "high"]
    employment_levels = ["unfavorable", "favorable"]
    cap_ratios = [0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]

    flex_map = {"low": "L", "mid": "M", "high": "H", "non_constrained": "N"}
    demand_map = {"mid": "M"}
    market_map = {"low": "L", "mid": "M", "high": "H"}

    config_count = 0

    def _base_copy(flex: str, market: str, employment: str, year: int) -> dict:
        cfg = base_config.copy()
        cfg.setdefault("aluminum", {})
        cfg["aluminum"].setdefault("current_scenario", {})
        cfg["aluminum"]["current_scenario"].update(
            {
                "smelter_flexibility": flex,
                "primary_demand": demand_level,
                "market_opportunity": market,
                "employment_transfer": employment,
            }
        )
        cfg["costs"]["year"] = year
        if "scenario" in cfg:
            cfg["scenario"]["planning_horizons"] = [year]
        return cfg

    fixed_flex = "mid"
    fixed_employment = "unfavorable"

    for market in market_levels:
        for year in years:
            fixed_suffix = f"{flex_map[fixed_flex]}{demand_map[demand_level]}{market_map[market]}{fixed_employment[:1].upper()}"

            # non-flexible (single flex/employment)
            cfg_non_flex = _base_copy(fixed_flex, market, fixed_employment, year)
            cfg_non_flex["version"] = f"{base_version}-{fixed_suffix}-{year}-non_flexible"
            cfg_non_flex["add_aluminum"] = False
            cfg_non_flex["only_other_load"] = False
            cfg_non_flex["aluminum"]["capacity_ratio"] = 0.0
            path_non_flex = configs_dir / f"config_{fixed_suffix}_{year}_non_flexible.yaml"
            _write_config(path_non_flex, cfg_non_flex)
            config_count += 1

            # no aluminum (single flex/employment)
            cfg_no_al = _base_copy(fixed_flex, market, fixed_employment, year)
            cfg_no_al["version"] = f"{base_version}-{fixed_suffix}-{year}-no_aluminum"
            cfg_no_al["add_aluminum"] = False
            cfg_no_al["only_other_load"] = True
            cfg_no_al["aluminum"]["capacity_ratio"] = 0.0
            path_no_al = configs_dir / f"config_{fixed_suffix}_{year}_no_aluminum.yaml"
            _write_config(path_no_al, cfg_no_al)
            config_count += 1

            # capacity ratios (full flex/employment)
            for flex in flexibility_levels:
                for employment in employment_levels:
                    scenario_suffix = (
                        f"{flex_map[flex]}{demand_map[demand_level]}{market_map[market]}{employment[:1].upper()}"
                    )
                    for cap_ratio in cap_ratios:
                        actual_capacity_ratio = _calculate_actual_capacity_ratio(year, cap_ratio, demand_level)
                        cap_percentage = int(cap_ratio * 100)
                        cfg = _base_copy(flex, market, employment, year)
                        cfg["version"] = f"{base_version}-{scenario_suffix}-{year}-{cap_percentage}p"
                        cfg["add_aluminum"] = True
                        cfg["only_other_load"] = True
                        cfg["aluminum_capacity_ratio"] = actual_capacity_ratio
                        cfg["aluminum"]["capacity_ratio"] = actual_capacity_ratio
                        if flex == "non_constrained":
                            cfg["iterative_optimization"] = False
                            cfg["aluminum_commitment"] = False
                        path = configs_dir / f"config_{scenario_suffix}_{year}_{cap_percentage}p.yaml"
                        _write_config(path, cfg)
                        config_count += 1

    # extra scenarios: mid flex/demand/market, favorable employment, no heat coupling (2050 only)
    extra_flex = "mid"
    extra_market = "mid"
    extra_employment = "favorable"
    extra_year = 2050
    if extra_year in years:
        extra_suffix = (
            f"{flex_map[extra_flex]}{demand_map[demand_level]}{market_map[extra_market]}{extra_employment[:1].upper()}"
        )
        for cap_ratio in cap_ratios:
            actual_capacity_ratio = _calculate_actual_capacity_ratio(extra_year, cap_ratio, demand_level)
            cap_percentage = int(cap_ratio * 100)
            cfg = _base_copy(extra_flex, extra_market, extra_employment, extra_year)
            cfg["version"] = f"{base_version}-{extra_suffix}-{extra_year}-{cap_percentage}p-no_heat"
            cfg["add_aluminum"] = True
            cfg["only_other_load"] = True
            cfg["aluminum_capacity_ratio"] = actual_capacity_ratio
            cfg["aluminum"]["capacity_ratio"] = actual_capacity_ratio
            cfg["heat_coupling"] = False
            path = configs_dir / f"config_{extra_suffix}_{extra_year}_{cap_percentage}p_no_heat.yaml"
            _write_config(path, cfg)
            config_count += 1

    print(f"Generated {config_count} config file(s) in total")


def _parse_years(value: str) -> List[int]:
    return [int(v.strip()) for v in value.split(",") if v.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate test configs")
    parser.add_argument(
        "--mode",
        choices=["value", "capacity", "all"],
        default="all",
        help="Default: all",
    )
    parser.add_argument("--years", default="2030,2040,2050", help="Comma-separated years")
    parser.add_argument("--clean", action="store_true", help="Clean configs dir before writing")
    args = parser.parse_args()

    years = _parse_years(args.years)

    if args.mode in ("value", "all"):
        run_value_tests(years=years, clean=args.clean)
    if args.mode in ("capacity", "all"):
        run_capacity_tests(years=years, clean=args.clean)

    print("=== Generating SLURM job files ===")
    try:
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "scripts/generate_slurm_jobs_advanced.py"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("✓ Successfully generated SLURM job files")
            if "co-generated" in result.stdout:
                for line in result.stdout.split("\n"):
                    if "co-generated" in line and "a SLURM job file" in line:
                        print(line.strip())
                        break
        else:
            print(f"✗ Error generating SLURM job files: {result.stderr}")
            print("Please run manually: python scripts/generate_slurm_jobs_advanced.py")
    except Exception as e:
        print(f"✗ Error generating SLURM job files: {e}")
        print("Please run manually: python scripts/generate_slurm_jobs_advanced.py")


if __name__ == "__main__":
    main()
