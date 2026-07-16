#!/usr/bin/env python3
"""Prepare and optionally run storage availability sensitivity cases.

Each case scales ``storage_capacity_guard.target_capacity_multiplier`` and writes
an independent config/result version. Local mode runs Snakemake, fills
``solar_value_dataset.xlsx`` for that case, then runs storage-summary and
thermal-flexibility post-processing.
"""

from __future__ import annotations

import argparse
import copy
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BATTERY_COST_FACTORS = {
    0.7: 1.0,
    1.0: 1.0,
    1.5: 1.0,
    2.0: 1.0,
}


def _tag(value: float) -> str:
    return f"{value:g}".replace("-", "m").replace(".", "p")


def _battery_cost_factor(multiplier: float, explicit: dict[float, float] | None = None) -> float:
    mapping = explicit or DEFAULT_BATTERY_COST_FACTORS
    for key, value in mapping.items():
        if abs(float(multiplier) - float(key)) < 1e-9:
            return float(value)
    return 1.0


def _first(value: Any, default: str) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else default
    if value is None:
        return default
    return str(value)


def _version_dir(config: dict[str, Any], root: Path) -> Path:
    results_dir = root / str(config.get("results_dir") or "results")
    return results_dir / f"version-{config['version']}"


def _scenario_stem(config: dict[str, Any]) -> str:
    scen = config.get("scenario") or {}
    opts = _first(scen.get("opts"), "ll")
    topology = str(scen.get("topology") or "current+FCG")
    pathway = _first(scen.get("pathway"), "linear2050")
    return f"{opts}-{topology}-{pathway}"


def _heating(config: dict[str, Any]) -> str:
    return _first((config.get("scenario") or {}).get("heating_demand"), "positive")


def _find_template_workbook(config: dict[str, Any], root: Path) -> Path | None:
    direct = _version_dir(config, root) / "solar_value_dataset.xlsx"
    if direct.is_file():
        return direct
    results_dir = root / "results"
    candidates = [p for p in results_dir.rglob("solar_value_dataset.xlsx") if p.is_file()]
    if not candidates:
        return None

    non_storage = [p for p in candidates if "-storage-" not in p.parent.name]
    pool = non_storage or candidates
    return max(pool, key=lambda p: p.stat().st_mtime)


def _usable_template_workbook(
    requested: Path | None,
    config: dict[str, Any],
    root: Path,
    *,
    destination: Path | None = None,
) -> Path | None:
    if requested is not None and requested.is_file():
        return requested

    candidate = _find_template_workbook(config, root)
    if candidate is None:
        return None
    if destination is not None and candidate.resolve() == destination.resolve():
        return None
    return candidate


def _run(cmd: list[str], *, cwd: Path) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate or locally run storage availability sensitivity cases."
    )
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    ap.add_argument("--multipliers", type=float, nargs="+", default=[0.7, 1.0, 1.5, 2.0])
    ap.add_argument(
        "--battery-cost-factors",
        type=float,
        nargs="*",
        default=None,
        help=(
            "Optional battery capital-cost factors paired with --multipliers. "
            "Default mapping: 0.7->1.0, 1.0->1.0, 1.5->1.0, 2.0->1.0."
        ),
    )
    ap.add_argument("--config-dir", type=Path, default=ROOT / "configs" / "storage_availability_sensitivity")
    ap.add_argument("--version-prefix", default=None)
    ap.add_argument("--template-workbook", type=Path, default=None)
    ap.add_argument("--cores", type=int, default=32)
    ap.add_argument(
        "--fill-price-mode",
        choices=["planning-marginal", "mapped-csv", "allow-zero-price"],
        default="allow-zero-price",
        help=(
            "Price source used when filling solar_value_dataset.xlsx after each storage run. "
            "Default uses the mapped price sidecar with zero-price hours preserved, matching "
            "the thermal-flexibility 40%% baseline configured by daily_low_output_zero_threshold=0.4."
        ),
    )
    ap.add_argument("--skip-plot", action="store_true")
    ap.add_argument("--run-local", action="store_true", help="Run all cases locally after generating files.")
    ap.add_argument(
        "--skip-summary",
        action="store_true",
        help="With --run-local, skip scripts/summarize_storage_availability_sensitivity.py.",
    )
    ap.add_argument(
        "--skip-thermal-flexibility",
        action="store_true",
        help="With --run-local, skip scripts/run_thermal_flexibility_sensitivity.py.",
    )
    args = ap.parse_args()

    config_path = args.config.resolve()
    with config_path.open(encoding="utf-8") as f:
        base_cfg = yaml.safe_load(f) or {}
    if "version" not in base_cfg:
        raise KeyError("Base config must define 'version'.")
    if any(m < 0 for m in args.multipliers):
        ap.error("--multipliers must be non-negative.")
    explicit_battery_factors = None
    if args.battery_cost_factors:
        if len(args.battery_cost_factors) != len(args.multipliers):
            ap.error("--battery-cost-factors must have the same length as --multipliers.")
        explicit_battery_factors = {
            float(m): float(f) for m, f in zip(args.multipliers, args.battery_cost_factors)
        }

    version_prefix = args.version_prefix or f"{base_cfg['version']}-storage"
    requested_template_workbook = args.template_workbook.resolve() if args.template_workbook else None
    template_workbook = _usable_template_workbook(requested_template_workbook, base_cfg, ROOT)
    if template_workbook is None:
        print("No solar_value_dataset.xlsx template found; local fill will require one before running.")

    args.config_dir.mkdir(parents=True, exist_ok=True)

    generated: list[tuple[float, float, Path, Path]] = []
    for multiplier in args.multipliers:
        tag = _tag(multiplier)
        case_name = f"x{tag}"
        case_cfg = copy.deepcopy(base_cfg)
        case_cfg["version"] = f"{version_prefix}-{case_name}"
        target_multiplier = float(multiplier)
        case_cfg.setdefault("storage_capacity_guard", {})["target_capacity_multiplier"] = float(target_multiplier)
        battery_factor = _battery_cost_factor(multiplier, explicit_battery_factors)
        market_mid = (
            case_cfg.setdefault("aluminum", {})
            .setdefault("scenario_dimensions", {})
            .setdefault("market_opportunity", {})
            .setdefault("mid", {})
        )
        market_mid["battery_cost_factor"] = float(battery_factor)
        sensitivity = case_cfg.setdefault("sensitivity", {})
        sensitivity["storage_availability_multiplier"] = float(target_multiplier)
        sensitivity["battery_cost_factor"] = float(battery_factor)
        sensitivity["thermal_flexibility_baseline"] = "threshold_0p4"
        sensitivity["thermal_flexibility_threshold"] = 0.4
        sensitivity["fill_price_mode"] = args.fill_price_mode

        case_config = args.config_dir / f"config_storage_{case_name}.yaml"
        with case_config.open("w", encoding="utf-8") as f:
            yaml.safe_dump(case_cfg, f, allow_unicode=True, sort_keys=False)

        version_dir = _version_dir(case_cfg, ROOT)
        generated.append((target_multiplier, battery_factor, case_config, version_dir))

    manifest = args.config_dir / "storage_availability_cases.csv"
    manifest.write_text(
        "multiplier,battery_cost_factor,thermal_flexibility_threshold,fill_price_mode,config,version_dir,scenario_stem,heating_demand\n"
        + "\n".join(
            f"{m},{bf},0.4,{args.fill_price_mode},{cfg},{vdir},{_scenario_stem(yaml.safe_load(cfg.read_text()) or {})},{_heating(yaml.safe_load(cfg.read_text()) or {})}"
            for m, bf, cfg, vdir in generated
        )
        + "\n",
        encoding="utf-8",
    )

    print("Generated storage availability sensitivity cases:")
    for multiplier, battery_factor, case_config, version_dir in generated:
        print(
            f"  {multiplier:g}x storage, battery cost {battery_factor:g}x -> "
            f"{case_config} | {version_dir}"
        )

    if args.run_local:
        template_workbook = _usable_template_workbook(requested_template_workbook, base_cfg, ROOT)
        if template_workbook is None:
            raise FileNotFoundError("Local fill requires --template-workbook or an existing result workbook.")
        price_arg = {
            "planning-marginal": "--planning-marginal",
            "mapped-csv": "--mapped-csv",
            "allow-zero-price": "--allow-zero-price",
        }[args.fill_price_mode]
        for _, _, case_config, version_dir in generated:
            _run(["snakemake", "--configfile", str(case_config), "--cores", str(args.cores)], cwd=ROOT)
            version_dir.mkdir(parents=True, exist_ok=True)
            workbook = version_dir / "solar_value_dataset.xlsx"
            template_workbook = _usable_template_workbook(
                requested_template_workbook,
                base_cfg,
                ROOT,
                destination=workbook,
            )
            if template_workbook is None:
                raise FileNotFoundError("No usable solar_value_dataset.xlsx template found under results/.")
            if template_workbook.resolve() != workbook.resolve():
                print(f"Using workbook template: {template_workbook}")
                shutil.copy2(template_workbook, workbook)
            fill_cmd = [
                sys.executable,
                "scripts/fill_solar_value_dataset_2025.py",
                "--config",
                str(case_config),
                price_arg,
            ]
            if args.skip_plot:
                fill_cmd.append("--skip-plot")
            _run(fill_cmd, cwd=ROOT)

        if not args.skip_summary:
            _run(
                [
                    sys.executable,
                    "scripts/summarize_storage_availability_sensitivity.py",
                    "--manifest",
                    str(manifest),
                ],
                cwd=ROOT,
            )

        if not args.skip_thermal_flexibility:
            storage_x1 = next(
                (
                    (case_config, version_dir)
                    for multiplier, _, case_config, version_dir in generated
                    if abs(float(multiplier) - 1.0) < 1e-9
                ),
                None,
            )
            if storage_x1 is None:
                print(
                    "Skip thermal flexibility sensitivity: no 1.0x storage case was generated in this run."
                )
            else:
                storage_x1_config, storage_x1_version_dir = storage_x1
                storage_x1_workbook = storage_x1_version_dir / "solar_value_dataset.xlsx"
                thermal_cmd = [
                    sys.executable,
                    "scripts/run_thermal_flexibility_sensitivity.py",
                    "--config",
                    str(storage_x1_config),
                ]
                if storage_x1_workbook.is_file():
                    thermal_cmd += ["--template-workbook", str(storage_x1_workbook)]
                _run(thermal_cmd, cwd=ROOT)


if __name__ == "__main__":
    main()
