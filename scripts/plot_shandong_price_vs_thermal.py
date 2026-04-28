"""
Plot full-year relationship between (reconstructed price, thermal dispatch) for Shandong.

Outputs:
- Scatter: price vs thermal output (coal+gas)
- Time series: price and thermal output over the year (dual axis)

Usage:
  conda run -n pypsa-china python scripts/plot_shandong_price_vs_thermal.py \\
    --network results/.../postnetwork-....nc \\
    --province Shandong \\
    --out-prefix results/.../shandong_price_vs_thermal
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib.pyplot as plt
import pypsa

import sys

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from reconstruct_market_prices import ReconstructPriceConfig, marginal_retail_prices  # noqa: E402


def _thermal_mask(n: pypsa.Network) -> pd.Series:
    car = n.generators.carrier.astype(str).str.lower()
    return car.str.contains("coal", regex=False) | car.str.contains("gas", regex=False)


def _shandong_thermal_dispatch(n: pypsa.Network, snapshots: pd.Index, province: str) -> pd.Series:
    """
    Sum coal+gas generator dispatch attributed to the province electricity bus.

    Notes:
    - In this repo, thermal generators may sit on buses like:
      - 'Shandong' (electricity bus)
      - 'Shandong coal' / 'Shandong gas' (fuel buses)
    - For a quick diagnostic, we include any generator whose bus string contains the province name.
      (This avoids missing 'Shandong coal' buses.)
    """
    mask_th = _thermal_mask(n)
    bus = n.generators.bus.astype(str)
    mask_bus = bus.str.contains(province, regex=False)
    gens = n.generators.index[mask_th & mask_bus]
    if len(gens) == 0:
        return pd.Series(0.0, index=snapshots, name="thermal_dispatch_MW")

    p = n.generators_t.p.reindex(index=snapshots, columns=gens)
    s = p.sum(axis=1)
    s.name = "thermal_dispatch_MW"
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", required=True, help="Path to solved network .nc")
    ap.add_argument("--province", default="Shandong", help="Province name (default: Shandong)")
    ap.add_argument("--out-prefix", required=True, help="Output prefix (no extension)")
    ap.add_argument("--week-freq", default="W-SUN", help="Week definition for weekly max (default: W-SUN)")
    ap.add_argument("--sample", type=int, default=0, help="Optional downsample to N points for scatter (0=all)")
    ap.add_argument(
        "--price-mode",
        default="marginal",
        choices=["marginal"],
        help="marginal: buses_t.marginal_price (mapped mode removed).",
    )
    ap.add_argument(
        "--currency",
        default="CNY",
        choices=["EUR", "CNY", "RMB"],
        help="Output currency for plotted/exported price series (default: CNY).",
    )
    ap.add_argument("--fx-cny-per-eur", type=float, default=7.8, help="FX used when currency=CNY/RMB.")
    args = ap.parse_args()

    n = pypsa.Network(args.network)
    cfg = ReconstructPriceConfig(week_freq=str(args.week_freq))

    prices = marginal_retail_prices(n, config=cfg)
    y_label = "Marginal price"
    if args.province not in prices.columns:
        raise SystemExit(f"Province '{args.province}' not found in reconstructed prices columns.")

    price = prices[args.province].copy()
    price.index = pd.to_datetime(price.index)
    cur = str(args.currency).upper()
    if cur in {"CNY", "RMB"}:
        price = price.astype(float) * float(args.fx_cny_per_eur)
        y_unit = "CNY/MWh"
    else:
        price = price.astype(float)
        y_unit = "EUR/MWh"

    th = _shandong_thermal_dispatch(n, price.index, args.province)
    th.index = pd.to_datetime(th.index)

    df = pd.concat([price.rename("price"), th], axis=1).dropna()

    out_prefix = Path(args.out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    # Optional downsample for scatter clarity
    if args.sample and args.sample > 0 and len(df) > args.sample:
        df_sc = df.sample(n=int(args.sample), random_state=0)
    else:
        df_sc = df

    # 1) Scatter
    plt.figure(figsize=(7.2, 5.2))
    plt.scatter(
        df_sc["thermal_dispatch_MW"].to_numpy(),
        df_sc["price"].to_numpy(),
        s=6,
        alpha=0.25,
        linewidths=0,
    )
    plt.xlabel("Thermal dispatch (coal+gas) [MW]")
    plt.ylabel(f"{y_label} [{y_unit}]")
    plt.title(f"{args.province}: full-year {args.price_mode} price vs thermal dispatch")
    plt.grid(True, alpha=0.25)
    plt.tight_layout()
    plt.savefig(out_prefix.with_suffix(".scatter.png"), dpi=180)

    # 2) Time series (dual axis)
    plt.figure(figsize=(11, 4.8))
    ax1 = plt.gca()
    ax1.plot(df.index, df["price"], color="#d62728", linewidth=1.0, label="price")
    ax1.set_ylabel(f"{y_label} [{y_unit}]", color="#d62728")
    ax1.tick_params(axis="y", labelcolor="#d62728")
    ax1.grid(True, alpha=0.25)

    ax2 = ax1.twinx()
    ax2.plot(df.index, df["thermal_dispatch_MW"], color="#1f77b4", linewidth=0.8, alpha=0.85, label="thermal")
    ax2.set_ylabel("Thermal dispatch (coal+gas) [MW]", color="#1f77b4")
    ax2.tick_params(axis="y", labelcolor="#1f77b4")

    plt.title(f"{args.province}: full-year {args.price_mode} price and thermal dispatch")
    plt.tight_layout()
    plt.savefig(out_prefix.with_suffix(".timeseries.png"), dpi=180)

    # 3) Export the paired data
    df.to_csv(out_prefix.with_suffix(".csv"), index_label="snapshot")


if __name__ == "__main__":
    main()

