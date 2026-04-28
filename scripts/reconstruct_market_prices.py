"""
Price utilities for solved PyPSA networks.

The repo previously supported a heuristic price mapping from thermal utilization
(`reconstruct_market_prices`). That approach has been removed to avoid mixing
post-hoc bid curves with endogenous dispatch/LMPs.

Only **marginal/LMP-based** provincial prices are supported:
- `marginal_retail_prices`: reads `buses_t.marginal_price` at province AC buses.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ReconstructPriceConfig:
    # Kept for API compatibility; currently unused.
    week_freq: str = "W-SUN"


def _removed_mapping(*_args, **_kwargs):
    raise RuntimeError(
        "Mapped price reconstruction has been removed. "
        "Use `marginal_retail_prices` from `buses_t.marginal_price`."
    )


def _province_elec_buses(n) -> pd.Index:
    buses_df = n.buses
    if "carrier" in buses_df.columns:
        elec_buses = buses_df.index[(buses_df["carrier"].astype(str) == "AC")]
    else:
        elec_buses = buses_df.index
    elec_buses = pd.Index(elec_buses.astype(str))
    elec_buses = elec_buses[~elec_buses.str.contains(" ", regex=False)]
    if len(elec_buses) == 0:
        raise ValueError("No electricity (AC) province buses found for price reconstruction.")
    return elec_buses


_interp_bid_price = _removed_mapping
_is_thermal_carrier = _removed_mapping
_resolve_gen_province = _removed_mapping
_local_mapped_prices = _removed_mapping
_line_efficiency = _removed_mapping
_apply_cross_border_imports = _removed_mapping
reconstruct_market_prices = _removed_mapping


def marginal_retail_prices(n, *, config: ReconstructPriceConfig | None = None) -> pd.DataFrame:
    """
    Provincial prices from PyPSA energy-balance duals (`buses_t.marginal_price`).

    Use this for second-stage dispatch networks (e.g. segmented thermal bids) instead of
    `reconstruct_market_prices`, which applies a separate weekly thermal bid map and can
    double-count if combined with an already bid-segmented dispatch solve.

    No cross-border import adjustment: LMPs are taken directly at each province AC bus
    (transmission is already in the dispatch duals).

    Parameters
    ----------
    config :
        Accepted for API symmetry with `reconstruct_market_prices`; currently unused.
    """
    provinces = _province_elec_buses(n)
    if not hasattr(n, "buses_t") or not hasattr(n.buses_t, "marginal_price"):
        raise ValueError("Network has no `buses_t.marginal_price` (run an economic dispatch solve first).")
    mp = n.buses_t.marginal_price
    cols = [str(p) for p in provinces if str(p) in mp.columns]
    if not cols:
        raise ValueError(
            "No provincial AC buses found in `buses_t.marginal_price` columns. "
            f"Expected a subset of: {list(provinces)[:5]}..."
        )
    snapshots = mp.index
    return mp[cols].reindex(snapshots).fillna(0.0).clip(lower=0.0)
