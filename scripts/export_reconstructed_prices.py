"""
Export reconstructed electricity prices from a solved PyPSA network `.nc`.

This is intended to be called from Snakemake (preferred) or from CLI.

Output is a CSV shaped like:
- index: snapshots
- columns: selected provinces (electricity buses)
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Iterable

import pandas as pd
import pypsa

# Make sibling imports work regardless of how this script is executed
# (Snakemake `python scripts/...`, module execution, or IDE runners).
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from reconstruct_market_prices import ReconstructPriceConfig, reconstruct_market_prices  # noqa: E402


def _select_provinces(prices: pd.DataFrame, provinces: Iterable[str] | None) -> pd.DataFrame:
    if provinces is None:
        return prices
    prov = [p for p in map(str, provinces) if p]
    if not prov:
        return prices
    missing = [p for p in prov if p not in prices.columns]
    if missing:
        raise ValueError(f"Requested provinces not found in reconstructed prices: {missing[:10]}")
    return prices[prov]


def export_prices(*, network_path: str, out_csv: str, provinces: list[str] | None, week_freq: str) -> None:
    n = pypsa.Network(network_path)
    prices = reconstruct_market_prices(n, config=ReconstructPriceConfig(week_freq=week_freq))
    prices = _select_provinces(prices, provinces)

    out_path = Path(out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prices.to_csv(out_path, index_label="snapshot")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", required=True, help="Solved postnetwork .nc path")
    ap.add_argument("--out", required=True, help="Output CSV path")
    ap.add_argument("--province", action="append", default=None, help="Province to include (repeatable). If omitted, export all.")
    ap.add_argument("--week-freq", default="W-SUN", help="Week definition for weekly max (default: W-SUN)")
    args = ap.parse_args()

    export_prices(
        network_path=args.network,
        out_csv=args.out,
        provinces=args.province,
        week_freq=str(args.week_freq),
    )


if __name__ == "__main__":
    main()

