# SPDX-FileCopyrightText: 2026 Ruike Lyu
#
# SPDX-License-Identifier: MIT
"""Refresh Shandong negative-price outputs after price-export parameter changes."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "configs/generated_shandong_negative_price_0609.1H.1.yaml"
DEFAULT_DISPATCH_BASE = REPO_ROOT / "results/version-0609.1H.1/dispatch_segmented/positive"
DEFAULT_BASE = REPO_ROOT / "results/version-0609.1H.1/prices/dispatch_segmented/positive"
DEFAULT_OUT = REPO_ROOT / "data/shandong_negative_price/negative_price_frequency_anchor_years.csv"
YEARS = (2025, 2030, 2035)


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(cmd, cwd=REPO_ROOT, env=env, check=True)


def _price_targets(base: Path, years: tuple[int, ...]) -> list[str]:
    return [
        str((base / f"dispatch_segmented_prices-ll-current+FCG-linear2050-{year}.csv").relative_to(REPO_ROOT))
        for year in years
    ]


def _dispatch_targets(base: Path, years: tuple[int, ...]) -> list[str]:
    return [
        str((base / f"postnetwork-dispatch-seg-ll-current+FCG-linear2050-{year}.nc").relative_to(REPO_ROOT))
        for year in years
    ]


def summarize_negative_frequency(base: Path, out: Path, years: tuple[int, ...]) -> pd.DataFrame:
    rows: list[dict[str, float | int | str]] = []
    for year in years:
        for price_type, suffix in (("marginal", ""), ("mapped", "_mapped")):
            path = base / f"dispatch_segmented_prices-ll-current+FCG-linear2050-{year}{suffix}.csv"
            df = pd.read_csv(path)
            s = pd.to_numeric(df["Shandong"], errors="coerce").dropna()
            rows.append(
                {
                    "year": year,
                    "price_type": price_type,
                    "neg_hours": int((s < 0.0).sum()),
                    "negative_price_frequency": float((s < 0.0).mean()),
                    "min_cny_mwh": float(s.min()),
                    "mean_cny_mwh": float(s.mean()),
                }
            )
    result = pd.DataFrame(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out, index=False)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Regenerate the Shandong negative-price merged config, refresh "
            "price CSVs, and summarize negative-price frequencies."
        )
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dispatch-base", type=Path, default=DEFAULT_DISPATCH_BASE)
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--years", nargs="+", type=int, default=list(YEARS))
    parser.add_argument(
        "--rerun-dispatch",
        action="store_true",
        help="Re-solve dispatch before refreshing prices. Use after load/external-receiving changes.",
    )
    parser.add_argument("--skip-snakemake", action="store_true", help="Only rebuild the summary CSV.")
    args = parser.parse_args()

    years = tuple(args.years)
    if not args.skip_snakemake:
        _run(["conda", "run", "-n", "pypsa", "python", "scripts/make_shandong_negative_price_config.py"])
        env = os.environ.copy()
        env.setdefault("MPLCONFIGDIR", "/private/tmp")
        env.setdefault("FONTCONFIG_PATH", "/private/tmp")
        if args.rerun_dispatch:
            _run(["rm", "-rf", ".snakemake/scripts", "/private/tmp/snakemake-source-cache"])
            _run(
                [
                    "conda",
                    "run",
                    "-n",
                    "pypsa",
                    "snakemake",
                    "--runtime-source-cache-path",
                    "/private/tmp/snakemake-source-cache",
                    "--configfile",
                    str(args.config),
                    "-j",
                    "3",
                    "--force",
                    *_dispatch_targets(args.dispatch_base, years),
                ],
                env=env,
            )
        _run(
            [
                "conda",
                "run",
                "-n",
                "pypsa",
                "snakemake",
                "--runtime-source-cache-path",
                "/private/tmp/snakemake-source-cache",
                "--configfile",
                str(args.config),
                "-j",
                "3",
                "--force",
                *_price_targets(args.base, years),
            ],
            env=env,
        )

    result = summarize_negative_frequency(args.base, args.out, years)
    print(result.to_string(index=False))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
