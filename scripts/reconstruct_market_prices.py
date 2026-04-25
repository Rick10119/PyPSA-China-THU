"""
Reconstruct market-clearing prices from a solved PyPSA network.

This implements an ex-post "load-rate mapping" bidding rule for thermal units
using local thermal utilization.

Expected inputs (solved network):
- n.generators.p_nom_opt (or p_nom) for optimized capacities
- n.generators_t.p for dispatch
"""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ReconstructPriceConfig:
    # Week definition for "当周最大出力" (pandas offset alias).
    # 'W-SUN' = weeks ending on Sunday; choose what matches your reporting convention.
    week_freq: str = "W-SUN"

    thermal_keywords: tuple[str, ...] = ("coal", "gas")


def _interp_bid_price(load_rate: np.ndarray) -> np.ndarray:
    """
    Piecewise-linear bid curve (CNY/MWh) as a function of thermal load rate.

    Control points (x=load rate, y=price):
    - x <= 0.40 -> 0
    - 0.50 -> 200
    - 0.70 -> 380
    - 0.85 -> 450
    - 0.95 -> 600
    - x >= 1.00 -> 1500
    """
    x = np.array([0.0, 0.40, 0.50, 0.70, 0.85, 0.95, 1.00], dtype=float)
    y = np.array([0.0, 0.0, 200.0, 380.0, 450.0, 600.0, 1500.0], dtype=float)
    lr = np.clip(load_rate.astype(float), 0.0, 1.0)
    return np.interp(lr, x, y, left=y[0], right=y[-1])


def _is_thermal_carrier(carrier: pd.Series, cfg: ReconstructPriceConfig) -> pd.Series:
    c = carrier.astype(str).str.lower()
    is_th = False
    for k in cfg.thermal_keywords:
        is_th = is_th | c.str.contains(k, regex=False)
    return is_th


def reconstruct_market_prices(n, *, config: ReconstructPriceConfig | None = None) -> pd.DataFrame:
    """
    Reconstruct market prices for each snapshot and province electricity bus.

    New mode (as requested): ignore inter-provincial propagation. For each
    province, map

      load_rate(t) = thermal_dispatch(t) / max_thermal_dispatch(within_same_week)

    to a bid price via the piecewise-linear curve.

    Parameters
    ----------
    n : pypsa.Network
        Solved network.
    config : ReconstructPriceConfig, optional
        Tuning parameters.

    Returns
    -------
    pd.DataFrame
        Reconstructed prices (index=snapshots, columns=province electricity buses).
    """
    cfg = config or ReconstructPriceConfig()

    if not hasattr(n, "generators_t") or not hasattr(n.generators_t, "p"):
        raise ValueError("Network is missing `n.generators_t.p`.")

    snapshots = n.generators_t.p.index

    # In sector-coupled networks, `buses_t.marginal_price` may include many
    # non-electric buses (heat, H2, etc.). By default we reconstruct prices for
    # province-level electricity buses only.
    buses_df = n.buses
    if "carrier" in buses_df.columns:
        elec_buses = buses_df.index[(buses_df["carrier"].astype(str) == "AC")]
    else:
        elec_buses = buses_df.index
    # Province buses in this repo are typically plain province names without spaces.
    elec_buses = pd.Index(elec_buses.astype(str))
    elec_buses = elec_buses[~elec_buses.str.contains(" ", regex=False)]

    if len(elec_buses) == 0:
        raise ValueError("No electricity (AC) province buses found for price reconstruction.")

    buses = elec_buses

    gens = n.generators.copy()
    # Thermal generators: carrier contains coal/gas.
    is_th = _is_thermal_carrier(gens["carrier"], cfg)
    if not bool(is_th.any()):
        return pd.DataFrame(0.0, index=snapshots, columns=buses)

    # Attribute thermal generators to provinces by bus name:
    # - include generators at 'Province'
    # - also include at 'Province ...' (e.g. 'Shandong coal', 'Shandong gas')
    gen_bus = gens["bus"].astype(str)
    gen_province = gen_bus.where(gen_bus.isin(buses))
    # For bus like 'Shandong coal', take the first token before space.
    needs_split = gen_province.isna() & gen_bus.str.contains(" ", regex=False)
    gen_province = gen_province.fillna(gen_bus.where(needs_split).str.split(" ", n=1).str[0])
    gen_province = gen_province.where(gen_province.isin(buses))

    keep = is_th & gen_province.notna()
    gen_use = gens.index[keep]
    if len(gen_use) == 0:
        return pd.DataFrame(0.0, index=snapshots, columns=buses)

    p_th = n.generators_t.p.reindex(index=snapshots, columns=gen_use)
    # Group to province columns
    prov = gen_province.loc[gen_use]
    thermal_by_prov = p_th.T.groupby(prov).sum().T.reindex(columns=buses, fill_value=0.0)

    # Weekly max (per province) and load-rate
    snap_dt = pd.to_datetime(snapshots)
    week = snap_dt.to_period(cfg.week_freq)
    weekly_max = thermal_by_prov.groupby(week).transform("max")
    denom = weekly_max.where(weekly_max > 0.0)
    load_rate = thermal_by_prov.divide(denom).fillna(0.0).clip(0.0, 1.0)

    # Map load-rate to price
    out = pd.DataFrame(
        _interp_bid_price(load_rate.to_numpy(dtype=float)),
        index=thermal_by_prov.index,
        columns=thermal_by_prov.columns,
    )
    out = out.fillna(0.0)
    out[out < 0.0] = 0.0
    return out

