"""
Reconstruct market-clearing prices from a solved PyPSA network.

This module combines:
1) A local "bid curve" mapping from thermal utilization (weekly peak-normalized load rate)
2) A simple cross-province settlement rule:
   - If a province receives uncongested inflow from a neighbor line, it may import the
     neighbor's local mapped price, adjusted for line losses via `Line.efficiency`.

Notes
-----
- Province electricity buses are identified as `carrier == "AC"` and bus names without spaces.
- Thermal dispatch is summed for generators whose `carrier` contains `coal`/`gas` and whose
  `bus` resolves to a province name (including buses like "Shandong coal").
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ReconstructPriceConfig:
    week_freq: str = "W-SUN"
    thermal_keywords: tuple[str, ...] = ("coal", "gas")

    # Line congestion tolerance (MW): treat as uncongested if |p0| < s_nom_opt - eps
    line_cong_eps_mw: float = 1e-3

    # Ignore tiny flows when attributing imports (MW)
    min_inflow_mw: float = 1e-3

    # How to aggregate multiple uncongested imports into one consumer price:
    # - "min_offer": competitive import offers -> take minimum delivered price
    # - "max_offer": conservative upper bound -> take maximum delivered price
    import_agg: str = "min_offer"


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


def _resolve_gen_province(gen_bus: pd.Series, provinces: pd.Index) -> pd.Series:
    gen_bus = gen_bus.astype(str)
    prov = gen_bus.where(gen_bus.isin(provinces))
    needs_split = prov.isna() & gen_bus.str.contains(" ", regex=False)
    prov = prov.fillna(gen_bus.where(needs_split).str.split(" ", n=1).str[0])
    return prov.where(prov.isin(provinces))


def _local_mapped_prices(
    n,
    *,
    snapshots: pd.Index,
    provinces: pd.Index,
    cfg: ReconstructPriceConfig,
) -> pd.DataFrame:
    gens = n.generators
    is_th = _is_thermal_carrier(gens["carrier"], cfg)
    gen_prov = _resolve_gen_province(gens["bus"], provinces)
    keep = is_th & gen_prov.notna()
    gen_use = gens.index[keep]
    if len(gen_use) == 0:
        return pd.DataFrame(0.0, index=snapshots, columns=provinces)

    p_th = n.generators_t.p.reindex(index=snapshots, columns=gen_use)
    thermal_by_prov = p_th.T.groupby(gen_prov.loc[gen_use]).sum().T.reindex(columns=provinces, fill_value=0.0)

    snap_dt = pd.to_datetime(snapshots)
    week = snap_dt.to_period(cfg.week_freq)
    weekly_max = thermal_by_prov.groupby(week).transform("max")
    denom = weekly_max.where(weekly_max > 0.0)
    load_rate = thermal_by_prov.divide(denom).fillna(0.0).clip(0.0, 1.0)

    mapped = pd.DataFrame(
        _interp_bid_price(load_rate.to_numpy(dtype=float)),
        index=thermal_by_prov.index,
        columns=thermal_by_prov.columns,
    )
    return mapped.fillna(0.0).clip(lower=0.0)


def _line_efficiency(lines: pd.DataFrame) -> pd.Series:
    if "efficiency" in lines.columns:
        eta = lines["efficiency"].astype(float)
    else:
        eta = pd.Series(1.0, index=lines.index, dtype=float)
    # Clamp to (0,1] for numerical safety
    return eta.clip(lower=1e-9, upper=1.0)


def _apply_cross_border_imports(
    *,
    local_mapped: pd.DataFrame,
    n,
    provinces: pd.Index,
    cfg: ReconstructPriceConfig,
) -> pd.DataFrame:
    """
    For each snapshot, set consumer price to min(local_mapped, best import offer).

    Import offer from exporter e -> consumer c on an uncongested line is:
        offer = local_mapped[e] / eta
    where `eta` is PyPSA `Line.efficiency` (power arriving at bus1 relative to bus0).
    """
    if not hasattr(n, "lines_t") or not hasattr(n.lines_t, "p0"):
        return local_mapped
    if n.lines.empty:
        return local_mapped

    lines = n.lines
    bus0 = lines["bus0"].astype(str)
    bus1 = lines["bus1"].astype(str)

    prov_set = set(map(str, provinces))
    mask_endpoints = bus0.isin(prov_set) & bus1.isin(prov_set)
    if not bool(mask_endpoints.any()):
        return local_mapped

    line_names = lines.index[mask_endpoints]
    b0 = bus0.loc[line_names].to_numpy()
    b1 = bus1.loc[line_names].to_numpy()
    eta = _line_efficiency(lines.loc[line_names]).to_numpy(dtype=float)

    s_nom = (
        lines.loc[line_names, "s_nom_opt"]
        if "s_nom_opt" in lines.columns
        else lines.loc[line_names, "s_nom"]
    ).to_numpy(dtype=float)

    p0 = n.lines_t.p0.reindex(index=local_mapped.index, columns=line_names)

    out = local_mapped.copy()

    agg = cfg.import_agg
    if agg not in {"min_offer", "max_offer"}:
        raise ValueError("import_agg must be one of: min_offer, max_offer")

    # Precompute positions for speed
    pos = {p: i for i, p in enumerate(local_mapped.columns)}

    for ti, t in enumerate(local_mapped.index):
        p0_t = p0.loc[t].to_numpy(dtype=float)
        uncong = np.abs(p0_t) < (s_nom - float(cfg.line_cong_eps_mw))

        # Build per-consumer list of import offers
        offers: dict[str, list[float]] = {str(p): [] for p in provinces}

        for k in range(len(line_names)):
            if not uncong[k]:
                continue

            f = float(p0_t[k])
            if abs(f) < float(cfg.min_inflow_mw):
                continue

            eta_k = float(eta[k])
            if eta_k <= 0.0:
                continue

            c0, c1 = str(b0[k]), str(b1[k])
            # Power leaving bus0 towards bus1 is +f on bus0 side; receiving end is bus1.
            if f > 0.0 and c1 in pos:
                exp = c0
                if exp in pos:
                    px = float(local_mapped.iat[ti, pos[exp]])
                    offers[c1].append(px / eta_k)
            elif f < 0.0 and c0 in pos:
                exp = c1
                if exp in pos:
                    px = float(local_mapped.iat[ti, pos[exp]])
                    offers[c0].append(px / eta_k)

        # Apply aggregation per consumer province
        for p in provinces:
            ps = str(p)
            if not offers[ps]:
                continue
            imp = float(np.min(offers[ps])) if agg == "min_offer" else float(np.max(offers[ps]))
            loc = float(out.iat[ti, pos[ps]])
            out.iat[ti, pos[ps]] = float(min(loc, imp))

    return out


def reconstruct_market_prices(n, *, config: ReconstructPriceConfig | None = None) -> pd.DataFrame:
    """
    Reconstruct provincial electricity prices.

    Steps
    -----
    1) Compute each province's local mapped bid price from weekly-normalized thermal utilization.
    2) Adjust prices downward when cheaper uncongested imports are available from neighbors,
       accounting for line losses via `Line.efficiency`.
    """
    cfg = config or ReconstructPriceConfig()

    if not hasattr(n, "generators_t") or not hasattr(n.generators_t, "p"):
        raise ValueError("Network is missing `n.generators_t.p`.")

    provinces = _province_elec_buses(n)
    snapshots = n.generators_t.p.index

    local_mapped = _local_mapped_prices(n, snapshots=snapshots, provinces=provinces, cfg=cfg)
    out = _apply_cross_border_imports(local_mapped=local_mapped, n=n, provinces=provinces, cfg=cfg)
    return out
