#!/usr/bin/env python3
"""Collect solar_value_dataset.xlsx files from sensitivity runs into one folder.

The default destination is the storage-availability summary directory, with
separate subfolders for storage, thermal-flexibility, and wind-cost cases.
"""

from __future__ import annotations

import argparse
import csv
import shutil
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = (
    ROOT / "results" / "storage_availability_sensitivity_summary" / "collected_solar_value_files"
)
DEFAULT_STORAGE_MANIFEST = (
    ROOT / "configs" / "storage_availability_sensitivity" / "storage_availability_cases.csv"
)
DEFAULT_WIND_MANIFEST = ROOT / "configs" / "wind_cost_sensitivity" / "wind_cost_sensitivity_cases.csv"
DEFAULT_THERMAL_CONFIG = ROOT / "configs" / "storage_availability_sensitivity" / "config_storage_x1.yaml"


def _load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_path(value: str | Path) -> Path:
    text = str(value).replace("\\", "/")
    marker = "PyPSA-China-THU/"
    if marker in text:
        text = text.split(marker, 1)[1]
    path = Path(text)
    return path if path.is_absolute() else ROOT / path


def _version_dir(cfg: dict[str, Any]) -> Path:
    return ROOT / str(cfg.get("results_dir") or "results") / f"version-{cfg['version']}"


def _tag(value: float) -> str:
    return f"{value:g}".replace("-", "m").replace(".", "p")


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _copy_case(
    *,
    category: str,
    case: str,
    source: Path,
    output_dir: Path,
    overwrite: bool,
) -> dict[str, str]:
    destination_dir = output_dir / category
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"solar_value_dataset_{case}.xlsx"
    status = "missing"
    if source.is_file():
        if destination.exists() and not overwrite:
            status = "exists"
        else:
            shutil.copy2(source, destination)
            status = "copied"
    return {
        "category": category,
        "case": case,
        "status": status,
        "source": str(source),
        "destination": str(destination),
    }


def _collect_storage(manifest: Path, output_dir: Path, overwrite: bool) -> list[dict[str, str]]:
    rows = []
    for row in _read_csv_rows(manifest):
        cfg_path = _resolve_path(row["config"])
        cfg = _load_config(cfg_path) if cfg_path.is_file() else {}
        multiplier = float(
            row.get("multiplier")
            or (cfg.get("sensitivity") or {}).get("storage_availability_multiplier", 1.0)
        )
        case = f"storage_x{_tag(multiplier)}"
        version_dir = _resolve_path(row["version_dir"]) if row.get("version_dir") else _version_dir(cfg)
        rows.append(
            _copy_case(
                category="storage",
                case=case,
                source=version_dir / "solar_value_dataset.xlsx",
                output_dir=output_dir,
                overwrite=overwrite,
            )
        )
    return rows


def _collect_wind(manifest: Path, output_dir: Path, overwrite: bool) -> list[dict[str, str]]:
    rows = []
    for row in _read_csv_rows(manifest):
        cfg_path = _resolve_path(row["config"])
        cfg = _load_config(cfg_path)
        case = str(row.get("case") or cfg_path.stem.removeprefix("config_"))
        rows.append(
            _copy_case(
                category="wind",
                case=case,
                source=_version_dir(cfg) / "solar_value_dataset.xlsx",
                output_dir=output_dir,
                overwrite=overwrite,
            )
        )
    return rows


def _collect_thermal(config: Path, output_dir: Path, overwrite: bool) -> list[dict[str, str]]:
    cfg = _load_config(config)
    thermal_root = _version_dir(cfg) / "thermal_flexibility_sensitivity"
    rows = []
    if not thermal_root.is_dir():
        return [
            {
                "category": "thermal_flexibility",
                "case": "thermal_flexibility_sensitivity",
                "status": "missing",
                "source": str(thermal_root),
                "destination": str(output_dir / "thermal_flexibility"),
            }
        ]

    workbooks = sorted(thermal_root.glob("*/solar_value_dataset.xlsx"))
    if not workbooks:
        return [
            {
                "category": "thermal_flexibility",
                "case": "thermal_flexibility_sensitivity",
                "status": "missing",
                "source": str(thermal_root / "*/solar_value_dataset.xlsx"),
                "destination": str(output_dir / "thermal_flexibility"),
            }
        ]

    for workbook in workbooks:
        rows.append(
            _copy_case(
                category="thermal_flexibility",
                case=workbook.parent.name,
                source=workbook,
                output_dir=output_dir,
                overwrite=overwrite,
            )
        )
    return rows


def _write_manifest(rows: list[dict[str, str]], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = output_dir / "collected_solar_value_files.csv"
    with manifest.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["category", "case", "status", "source", "destination"],
        )
        writer.writeheader()
        writer.writerows(rows)
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Copy sensitivity solar_value_dataset.xlsx files into the storage summary folder."
    )
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    ap.add_argument("--storage-manifest", type=Path, default=DEFAULT_STORAGE_MANIFEST)
    ap.add_argument("--wind-manifest", type=Path, default=DEFAULT_WIND_MANIFEST)
    ap.add_argument("--thermal-config", type=Path, default=DEFAULT_THERMAL_CONFIG)
    ap.add_argument("--no-overwrite", action="store_true", help="Do not replace files already collected.")
    ap.add_argument("--strict", action="store_true", help="Fail if any expected workbook is missing.")
    args = ap.parse_args()

    output_dir = args.output_dir.resolve()
    overwrite = not args.no_overwrite
    rows: list[dict[str, str]] = []
    rows.extend(_collect_storage(args.storage_manifest.resolve(), output_dir, overwrite))
    rows.extend(_collect_thermal(args.thermal_config.resolve(), output_dir, overwrite))
    rows.extend(_collect_wind(args.wind_manifest.resolve(), output_dir, overwrite))
    manifest = _write_manifest(rows, output_dir)

    copied = sum(row["status"] == "copied" for row in rows)
    existing = sum(row["status"] == "exists" for row in rows)
    missing = [row for row in rows if row["status"] == "missing"]
    print(f"Collected solar-value files under: {output_dir}")
    print(f"Manifest: {manifest}")
    print(f"Copied: {copied}; already existed: {existing}; missing: {len(missing)}")
    if missing:
        print("Missing workbook(s):")
        for row in missing:
            print(f"  [{row['category']}] {row['case']}: {row['source']}")
        if args.strict:
            raise FileNotFoundError(f"{len(missing)} expected workbook(s) were missing.")


if __name__ == "__main__":
    main()
