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


def export_prices(
    *,
    network_path: str,
    out_csv: str,
    provinces: list[str] | None,
    week_freq: str,
    import_agg: str,
    line_cong_eps_mw: float,
    min_inflow_mw: float,
) -> None:
    n = pypsa.Network(network_path)
    prices = reconstruct_market_prices(
        n,
        config=ReconstructPriceConfig(
            week_freq=week_freq,
            import_agg=import_agg,
            line_cong_eps_mw=float(line_cong_eps_mw),
            min_inflow_mw=float(min_inflow_mw),
        ),
    )
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
    ap.add_argument(
        "--import-agg",
        default="min_offer",
        choices=["min_offer", "max_offer"],
        help="How to aggregate multiple uncongested import offers (default: min_offer)",
    )
    ap.add_argument("--line-cong-eps-mw", type=float, default=1e-3, help="Congestion slack in MW (default: 1e-3)")
    ap.add_argument("--min-inflow-mw", type=float, default=1e-3, help="Ignore smaller line flows (default: 1e-3)")
    args = ap.parse_args()

    export_prices(
        network_path=args.network,
        out_csv=args.out,
        provinces=args.province,
        week_freq=str(args.week_freq),
        import_agg=str(args.import_agg),
        line_cong_eps_mw=float(args.line_cong_eps_mw),
        min_inflow_mw=float(args.min_inflow_mw),
    )


if __name__ == "__main__":
    main()

