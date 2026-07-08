#!/usr/bin/env python3
"""Prepare and optionally run storage availability sensitivity cases.

Each case scales ``storage_capacity_guard.target_capacity_multiplier`` and writes
an independent config/result version. Slurm and local modes both run Snakemake,
then fill ``solar_value_dataset.xlsx`` for that case.
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
    0.7: 1.5,
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
    candidates = sorted(
        p
        for p in (root / "results").glob("version-*/solar_value_dataset.xlsx")
        if "-storage-" not in p.parent.name
    )
    return candidates[-1] if candidates else None


def _run(cmd: list[str], *, cwd: Path) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def _repo_display_path(path: Path) -> str:
    path = path.resolve()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _write_slurm_job(
    job_path: Path,
    *,
    name: str,
    config_path: Path,
    version_dir: Path,
    template_workbook: Path | None,
    cores: int,
    conda_env: str,
    time_limit: str,
    mem_per_cpu: str,
    mail_user: str,
    fill_price_mode: str,
    skip_plot: bool,
) -> None:
    plot_arg = " --skip-plot" if skip_plot else ""
    config_job_path = _repo_display_path(config_path)
    version_job_dir = _repo_display_path(version_dir)
    template_job_path = _repo_display_path(template_workbook) if template_workbook is not None else None
    template_block = ""
    if template_workbook is not None:
        template_block = f"""
mkdir -p "{version_job_dir}"
if [ "$(cd "$(dirname "{template_job_path}")" && pwd)/$(basename "{template_job_path}")" != "$(cd "{version_job_dir}" && pwd)/solar_value_dataset.xlsx" ]; then
    cp "{template_job_path}" "{version_job_dir}/solar_value_dataset.xlsx"
fi
"""
    price_arg = {
        "planning-marginal": "--planning-marginal",
        "mapped-csv": "--mapped-csv",
        "allow-zero-price": "--allow-zero-price",
    }[fill_price_mode]
    content = f"""#!/bin/bash
#SBATCH --job-name=storage-{name}
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task={cores}
#SBATCH --mem-per-cpu={mem_per_cpu}
#SBATCH --time={time_limit}
#SBATCH --mail-type=fail
#SBATCH --mail-user={mail_user}

mkdir -p logs
LOG_FILE="logs/storage_{name}_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Storage availability sensitivity: {name} ==="
echo "Start time: $(date)"
echo "Job ID: $SLURM_JOB_ID"
echo "Config: {config_job_path}"

module purge
module load anaconda3/2024.10
conda activate {conda_env}
module load gurobi/12.0.0

FORCE_RESTART="${{FORCE_RESTART:-0}}"
SNAKEMAKE_EXTRA_ARGS=""
if [ "$FORCE_RESTART" = "1" ]; then
    SNAKEMAKE_EXTRA_ARGS="--forceall --rerun-incomplete"
fi

snakemake --configfile "{config_job_path}" --cores {cores} $SNAKEMAKE_EXTRA_ARGS
{template_block}
python scripts/fill_solar_value_dataset_2025.py --config "{config_job_path}" {price_arg}{plot_arg}

echo "Finished: $(date)"
echo "Log file: $LOG_FILE"
"""
    job_path.write_text(content, encoding="utf-8")
    job_path.chmod(0o755)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate, submit, or locally run storage availability sensitivity cases."
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
            "Default mapping: 0.7->1.5, 1.0->1.0, 1.5->1.0, 2.0->1.0."
        ),
    )
    ap.add_argument("--config-dir", type=Path, default=ROOT / "configs" / "storage_availability_sensitivity")
    ap.add_argument("--job-dir", type=Path, default=ROOT / "jobs_storage_availability")
    ap.add_argument("--version-prefix", default=None)
    ap.add_argument("--template-workbook", type=Path, default=None)
    ap.add_argument("--cores", type=int, default=32)
    ap.add_argument("--conda-env", default="pypsa-china")
    ap.add_argument("--time-limit", default="71:59:00")
    ap.add_argument("--mem-per-cpu", default="20G")
    ap.add_argument("--mail-user", default="rl8728@princeton.edu")
    ap.add_argument(
        "--fill-price-mode",
        choices=["planning-marginal", "mapped-csv", "allow-zero-price"],
        default="allow-zero-price",
        help=(
            "Price source used when filling solar_value_dataset.xlsx after each storage run. "
            "Default uses the mapped price sidecar with zero-price hours preserved, matching "
            "the thermal-flexibility 40% baseline configured by daily_low_output_zero_threshold=0.4."
        ),
    )
    ap.add_argument("--skip-plot", action="store_true")
    ap.add_argument("--run-local", action="store_true", help="Run all cases locally after generating files.")
    ap.add_argument("--submit", action="store_true", help="Submit generated Slurm jobs with sbatch.")
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
    template_workbook = args.template_workbook.resolve() if args.template_workbook else _find_template_workbook(base_cfg, ROOT)
    if template_workbook is None:
        print("No solar_value_dataset.xlsx template found; generated jobs will expect one before filling.")

    args.config_dir.mkdir(parents=True, exist_ok=True)
    args.job_dir.mkdir(parents=True, exist_ok=True)

    generated: list[tuple[float, float, Path, Path, Path]] = []
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
        job_path = args.job_dir / f"job_storage_{case_name}.slurm"
        _write_slurm_job(
            job_path,
            name=case_name,
            config_path=case_config,
            version_dir=version_dir,
            template_workbook=template_workbook,
            cores=args.cores,
            conda_env=args.conda_env,
            time_limit=args.time_limit,
            mem_per_cpu=args.mem_per_cpu,
            mail_user=args.mail_user,
            fill_price_mode=args.fill_price_mode,
            skip_plot=args.skip_plot,
        )
        generated.append((target_multiplier, battery_factor, case_config, job_path, version_dir))

    manifest = args.config_dir / "storage_availability_cases.csv"
    manifest.write_text(
        "multiplier,battery_cost_factor,thermal_flexibility_threshold,fill_price_mode,config,job,version_dir,scenario_stem,heating_demand\n"
        + "\n".join(
            f"{m},{bf},0.4,{args.fill_price_mode},{cfg},{job},{vdir},{_scenario_stem(yaml.safe_load(cfg.read_text()) or {})},{_heating(yaml.safe_load(cfg.read_text()) or {})}"
            for m, bf, cfg, job, vdir in generated
        )
        + "\n",
        encoding="utf-8",
    )

    print("Generated storage availability sensitivity cases:")
    for multiplier, battery_factor, case_config, job_path, version_dir in generated:
        print(
            f"  {multiplier:g}x storage, battery cost {battery_factor:g}x -> "
            f"{case_config} | {job_path} | {version_dir}"
        )

    if args.submit:
        for _, _, _, job_path, _ in generated:
            _run(["sbatch", str(job_path)], cwd=ROOT)

    if args.run_local:
        if template_workbook is None or not template_workbook.is_file():
            raise FileNotFoundError("Local fill requires --template-workbook or an existing result workbook.")
        price_arg = {
            "planning-marginal": "--planning-marginal",
            "mapped-csv": "--mapped-csv",
            "allow-zero-price": "--allow-zero-price",
        }[args.fill_price_mode]
        for _, _, case_config, _, version_dir in generated:
            _run(["snakemake", "--configfile", str(case_config), "--cores", str(args.cores)], cwd=ROOT)
            version_dir.mkdir(parents=True, exist_ok=True)
            workbook = version_dir / "solar_value_dataset.xlsx"
            if template_workbook.resolve() != workbook.resolve():
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


if __name__ == "__main__":
    main()
