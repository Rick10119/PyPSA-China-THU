"""
Export reconstructed electricity prices from a solved PyPSA network `.nc`.

This is intended to be called from Snakemake (preferred) or from CLI.

Output CSVs (same snapshot index, selected provinces unless --province is omitted for nodal):
- Primary: marginal (provincial) prices from `buses_t.marginal_price`
- Sidecar: mapped reconstruction from segmented thermal bids (+ export adjustments)
- Sidecar: **nodal** marginal prices — full `buses_t.marginal_price` (all buses with duals),
  or the same column subset as `--province` when provinces are restricted

When plotting is enabled and Shandong is among exported provinces, PNGs include full-year scatter
/ time series (existing) plus a 2×2 grid of **one random day per meteorological season** for
mapped price vs thermal dispatch (`*.seasonal_random_days.png`).
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Iterable

import numpy as np
import pandas as pd
import pypsa
import yaml

# Make sibling imports work regardless of how this script is executed
# (Snakemake `python scripts/...`, module execution, or IDE runners).
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from reconstruct_market_prices import (  # noqa: E402
    ReconstructPriceConfig,
    marginal_retail_prices,
)
from plot_shandong_price_vs_thermal import (  # noqa: E402
    export_price_vs_thermal_plots,
    export_seasonal_random_day_profiles,
)


def _default_config_path() -> Path:
    return _THIS_DIR.parent / "config.yaml"


def _load_mapped_carrier_config(config_path: str | Path | None = None) -> tuple[set[str], dict[str, str]]:
    """
    Load carrier filters for mapped-price reconstruction from config:
    dispatch_segmented_prices.mapped_carriers.{Generator,Link}
    (fallback: dispatch_segmented_prices.carriers.{Generator,Link}).

    Returns:
      (generator_carriers, link_carrier_to_bus1_carrier)
    """
    cfg_path = Path(config_path) if config_path is not None else _default_config_path()
    if not cfg_path.exists():
        raise FileNotFoundError(f"Mapped carrier config not found: {cfg_path}")

    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    dsp = cfg.get("dispatch_segmented_prices", {}) or {}
    carriers = dsp.get("mapped_carriers", {}) or dsp.get("carriers", {}) or {}
    gen_cfg = carriers.get("Generator", {}) or {}
    link_cfg = carriers.get("Link", {}) or {}
    if not isinstance(gen_cfg, dict) or not isinstance(link_cfg, dict):
        raise ValueError(
            "Invalid mapped carrier config: expected "
            "dispatch_segmented_prices.mapped_carriers.{Generator,Link} "
            "(or fallback dispatch_segmented_prices.carriers.{Generator,Link}) "
            "to be mappings."
        )

    generator_carriers = {str(k) for k in gen_cfg.keys()}
    if not generator_carriers:
        raise ValueError(
            "Mapped carrier config requires at least one carrier under "
            "dispatch_segmented_prices.mapped_carriers.Generator "
            "(or fallback dispatch_segmented_prices.carriers.Generator)."
        )
    link_carrier_to_bus1_carrier: dict[str, str] = {}
    for k, v in link_cfg.items():
        c = str(k)
        only_bus1 = ""
        if isinstance(v, dict):
            only_bus1 = str(v.get("only_bus1_carrier", "") or "")
        link_carrier_to_bus1_carrier[c] = only_bus1
    if not link_carrier_to_bus1_carrier:
        raise ValueError(
            "Mapped carrier config requires at least one carrier under "
            "dispatch_segmented_prices.mapped_carriers.Link "
            "(or fallback dispatch_segmented_prices.carriers.Link)."
        )

    return generator_carriers, link_carrier_to_bus1_carrier


def _load_mapped_price_control_points(
    config_path: str | Path | None = None,
) -> tuple[np.ndarray, np.ndarray] | None:
    """
    Load mapped-price control points from config.

    Supported locations:
    - dispatch_segmented_prices.price_export.control_points: {x: [...], y: [...]}
    - dispatch_segmented_prices.price_export.mapped_price_control_points: {x: [...], y: [...]}
    - dispatch_segmented_prices.control_points / mapped_price_control_points

    If explicit control points are absent, fallback to
    `dispatch_segmented_prices.carriers` segment config and construct:
      x = [0, cumulative(shares)]
      y = [0, 0, marginal_cost[1], ..., marginal_cost[-1]]
    so the first segment is flat at zero and subsequent segments are linear.

    Returns (x, y) if present/derived and valid, else None.
    Rule required by reconstruction:
    - The first segment price is always 0, so the first two y-knots are forced to 0.
    """
    cfg_path = Path(config_path) if config_path is not None else _default_config_path()
    if not cfg_path.exists():
        return None

    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    dsp = cfg.get("dispatch_segmented_prices", {}) or {}
    pe = dsp.get("price_export", {}) or {}

    cand = (
        pe.get("control_points")
        or pe.get("mapped_price_control_points")
        or dsp.get("control_points")
        or dsp.get("mapped_price_control_points")
    )
    x_raw: list[float] | None = None
    y_raw: list[float] | None = None
    if isinstance(cand, dict):
        x0 = cand.get("x")
        y0 = cand.get("y")
        if isinstance(x0, (list, tuple)) and isinstance(y0, (list, tuple)) and len(x0) == len(y0) and len(x0) > 0:
            x_raw = [float(v) for v in x0]
            y_raw = [float(v) for v in y0]

    # Fallback: derive curve from segmented carrier config.
    if x_raw is None or y_raw is None:
        carriers_cfg = dsp.get("carriers", {}) or {}
        gen_cfg = carriers_cfg.get("Generator", {}) or {}
        link_cfg = carriers_cfg.get("Link", {}) or {}
        specs: list[dict] = []
        if isinstance(gen_cfg, dict):
            specs.extend(v for v in gen_cfg.values() if isinstance(v, dict))
        if isinstance(link_cfg, dict):
            specs.extend(v for v in link_cfg.values() if isinstance(v, dict))

        seg_spec = next(
            (
                s
                for s in specs
                if isinstance(s.get("shares"), (list, tuple))
                and isinstance(s.get("marginal_cost"), (list, tuple))
                and len(s.get("shares")) == len(s.get("marginal_cost"))
                and len(s.get("shares")) >= 2
            ),
            None,
        )
        if seg_spec is None:
            return None

        shares = np.asarray([float(v) for v in seg_spec.get("shares", [])], dtype=float)
        mc = np.asarray([float(v) for v in seg_spec.get("marginal_cost", [])], dtype=float)
        ssum = float(np.sum(shares))
        if shares.size < 2 or mc.size != shares.size or ssum <= 0.0:
            return None
        cum = np.clip(np.cumsum(shares / ssum), 0.0, 1.0)
        x_raw = [0.0] + cum.tolist()
        y_raw = [0.0, 0.0] + [max(float(v), 0.0) for v in mc[1:].tolist()]

    x = np.asarray([float(v) for v in x_raw], dtype=float)
    y = np.asarray([max(float(v), 0.0) for v in y_raw], dtype=float)

    # Sort by x and clip to feasible load-ratio range.
    order = np.argsort(x)
    x = np.clip(x[order], 0.0, 1.0)
    y = y[order]

    # Ensure the curve starts at LR=0.
    if x[0] > 0.0:
        x = np.concatenate(([0.0], x))
        y = np.concatenate(([0.0], y))
    else:
        x[0] = 0.0

    # Merge duplicate x knots (keep last y), required by np.interp.
    x_list: list[float] = []
    y_list: list[float] = []
    for xi, yi in zip(x.tolist(), y.tolist()):
        if x_list and xi <= x_list[-1] + 1e-15:
            x_list[-1] = float(xi)
            y_list[-1] = float(yi)
        else:
            x_list.append(float(xi))
            y_list.append(float(yi))

    x = np.asarray(x_list, dtype=float)
    y = np.asarray(y_list, dtype=float)
    if x.size == 0:
        return None

    # Enforce mapped-price rule:
    # first segment is all zeros, and only later segments are linear.
    y[0] = 0.0
    if y.size >= 2:
        y[1] = 0.0

    # Ensure right boundary exists.
    if x[-1] < 1.0:
        x = np.concatenate((x, [1.0]))
        y = np.concatenate((y, [y[-1]]))

    return x, y


def _load_mapped_supply_curve_settings(
    config_path: str | Path | None = None,
) -> dict | None:
    """
    Optional piecewise linear mapping: mapped_price = mult(lr) * province_ref_fuel_eur_mwh_el.

    Config: ``dispatch_segmented_prices.price_export.mapped_supply_curve`` with:
    - lr_threshold_first: load ratio (after monthly norm) up to this value → price mult 0
    - mult_at_bandwidth_start: mult just above lr_threshold_first (typically 1.0 = 100% fuel)
    - lr_knots: upper bounds of linear pieces (ascending, last should be 1.0)
    - mult_at_knots: multiplier at each knot (same length as lr_knots)

    If this block is absent, fall back to control-point / merit-order curves.
    """
    cfg_path = Path(config_path) if config_path is not None else _default_config_path()
    if not cfg_path.exists():
        return None

    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    dsp = cfg.get("dispatch_segmented_prices", {}) or {}
    pe = dsp.get("price_export", {}) or {}
    cand = pe.get("mapped_supply_curve") or {}
    if not isinstance(cand, dict):
        return None

    ks = cand.get("lr_knots")
    ms = cand.get("mult_at_knots")
    t0 = cand.get("lr_threshold_first")
    if not isinstance(ks, (list, tuple)) or not isinstance(ms, (list, tuple)):
        return None
    if len(ks) != len(ms) or len(ks) < 1:
        return None
    if t0 is None:
        return None
    try:
        t0_f = float(t0)
        knots = [float(v) for v in ks]
        mults = [float(v) for v in ms]
        m_start = float(cand.get("mult_at_bandwidth_start", 1.0))
    except (TypeError, ValueError):
        return None
    if t0_f < 0 or t0_f > 1.0 or knots != sorted(knots):
        raise ValueError("mapped_supply_curve: lr_threshold_first must be in [0,1] and lr_knots ascending.")
    if knots[0] <= t0_f:
        raise ValueError("mapped_supply_curve: lr_knots[0] must be greater than lr_threshold_first.")

    return {
        "lr_threshold_first": t0_f,
        "lr_knots": knots,
        "mult_at_knots": mults,
        "mult_at_bandwidth_start": m_start,
    }


def _mapped_multiplier_from_lr_normalized(lr: np.ndarray, s: dict) -> np.ndarray:
    """Piecewise-linear multiplier vs normalized load ratio (see _load_mapped_supply_curve_settings)."""
    t0 = float(s["lr_threshold_first"])
    knots: list[float] = list(s["lr_knots"])
    mults: list[float] = list(s["mult_at_knots"])
    m_start = float(s.get("mult_at_bandwidth_start", 1.0))
    lr_clip = np.clip(np.asarray(lr, dtype=float), 0.0, 1.0 + 1e-12)
    out = np.zeros_like(lr_clip, dtype=float)
    mask_le = lr_clip <= t0
    out[mask_le] = 0.0
    active = lr_clip > t0
    lo = t0
    m_lo = m_start
    for hi, m_hi in zip(knots, mults):
        seg = active & (lr_clip > lo) & (lr_clip <= hi)
        if np.any(seg):
            out[seg] = m_lo + (lr_clip[seg] - lo) / max(hi - lo, 1e-15) * (m_hi - m_lo)
        lo = hi
        m_lo = m_hi
    tail = lr_clip > knots[-1]
    if np.any(tail):
        out[tail] = mults[-1]
    return np.clip(out, 0.0, np.inf)


def _province_ref_fuel_eur_from_seg0_network(
    n: pypsa.Network,
    province: str,
    *,
    generator_carriers: set[str],
    link_carrier_to_bus1_carrier: dict[str, str],
) -> float | None:
    """Capacity-weighted marginal cost on ``__seg0`` splits (EUR/MWh_el), interpreted as bid fuel tier."""
    prov_set = {str(province)}
    wsum = 0.0
    capsum = 0.0

    if hasattr(n, "generators") and not n.generators.empty:
        gen = n.generators
        for g, row in gen.iterrows():
            if not str(g).endswith("__seg0"):
                continue
            if not _generator_selected(row.get("carrier", ""), generator_carriers):
                continue
            pbus = _resolve_bus_province(str(row.get("bus", "")), prov_set)
            if pbus is None:
                continue
            cap = float(pd.to_numeric(row.get("p_nom", 0.0), errors="coerce") or 0.0)
            mc = float(pd.to_numeric(row.get("marginal_cost", 0.0), errors="coerce") or 0.0)
            if cap > 1e-9 and mc >= 0.0:
                wsum += cap * mc
                capsum += cap

    if hasattr(n, "links") and not n.links.empty:
        links = n.links
        for l, row in links.iterrows():
            if not str(l).endswith("__seg0"):
                continue
            if not _link_selected(n, row, link_carrier_to_bus1_carrier):
                continue
            pbus = _resolve_bus_province(str(row.get("bus1", "")), prov_set)
            if pbus is None:
                continue
            p_nom = float(pd.to_numeric(row.get("p_nom", 0.0), errors="coerce") or 0.0)
            eta = float(pd.to_numeric(row.get("efficiency", 1.0), errors="coerce") or 1.0)
            cap = p_nom * max(eta, 0.0)
            mc = float(pd.to_numeric(row.get("marginal_cost", 0.0), errors="coerce") or 0.0)
            if cap > 1e-9 and mc >= 0.0:
                wsum += cap * mc
                capsum += cap

    if capsum <= 1e-9:
        return None
    return float(wsum / capsum)


def _province_ref_fuel_eur_from_blocks(blocks: list[tuple[float, float]]) -> float | None:
    """Fallback: capacity-weighted mean marginal cost from plant-level blocks."""
    if not blocks:
        return None
    w = 0.0
    s = 0.0
    for cap, mc in blocks:
        c = max(float(cap), 0.0)
        if c <= 0.0:
            continue
        w += c * float(mc)
        s += c
    if s <= 1e-9:
        return None
    return float(w / s)


def _province_elec_buses(n: pypsa.Network) -> pd.Index:
    buses_df = n.buses
    if "carrier" in buses_df.columns:
        elec_buses = buses_df.index[(buses_df["carrier"].astype(str) == "AC")]
    else:
        elec_buses = buses_df.index
    elec_buses = pd.Index(elec_buses.astype(str))
    elec_buses = elec_buses[~elec_buses.str.contains(" ", regex=False)]
    if len(elec_buses) == 0:
        raise ValueError("No electricity (AC) province buses found for mapped reconstruction.")
    return elec_buses


def _all_bus_marginal_prices(n: pypsa.Network) -> pd.DataFrame:
    """Every column in `buses_t.marginal_price`, numeric and clipped like `marginal_retail_prices`."""
    if not hasattr(n, "buses_t") or not hasattr(n.buses_t, "marginal_price"):
        raise ValueError(
            "Network has no `buses_t.marginal_price` (run an economic dispatch solve first)."
        )
    mp = n.buses_t.marginal_price
    return mp.apply(pd.to_numeric, errors="coerce").fillna(0.0).clip(lower=0.0).astype(float)


def _select_nodal_marginal(nodal: pd.DataFrame, provinces: list[str] | None) -> pd.DataFrame:
    if provinces is None:
        return nodal
    prov = [p for p in map(str, provinces) if p]
    if not prov:
        return nodal
    missing = [p for p in prov if p not in nodal.columns]
    if missing:
        raise ValueError(f"Requested provinces not found in nodal marginal_price: {missing[:10]}")
    return nodal[prov]


def _calibrate_nodal_with_baseline(nodal: pd.DataFrame, n_baseline: pypsa.Network) -> pd.DataFrame:
    base = _all_bus_marginal_prices(n_baseline)
    base = base.reindex(index=nodal.index, columns=nodal.columns).fillna(0.0).astype(float)
    disp = nodal.astype(float)
    return disp.mask(disp < base, base)


def _resolve_bus_province(bus_name: str, provinces: set[str]) -> str | None:
    b = str(bus_name)
    head = b.split(" ", 1)[0]
    if head in provinces:
        return head
    if b in provinces:
        return b
    return None


def _safe_series(df: pd.DataFrame, col: str, index: pd.Index) -> pd.Series:
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce").reindex(index).fillna(0.0).astype(float)
    return pd.Series(0.0, index=index, dtype=float)


def _generator_selected(carrier: str, generator_carriers: set[str]) -> bool:
    return str(carrier) in generator_carriers


def _link_selected(
    n: pypsa.Network,
    row: pd.Series,
    link_carrier_to_bus1_carrier: dict[str, str],
) -> bool:
    c = str(row.get("carrier", ""))
    if c not in link_carrier_to_bus1_carrier:
        return False
    bus1_req = str(link_carrier_to_bus1_carrier.get(c, "") or "")
    if not bus1_req:
        return True
    b1 = str(row.get("bus1", ""))
    if not b1 or not hasattr(n, "buses") or b1 not in n.buses.index:
        return False
    return str(n.buses.at[b1, "carrier"]) == bus1_req


def _infer_local_thermal_dispatch(
    n: pypsa.Network,
    provinces: pd.Index,
    snapshots: pd.Index,
    *,
    generator_carriers: set[str],
    link_carrier_to_bus1_carrier: dict[str, str],
) -> pd.DataFrame:
    prov_set = set(map(str, provinces))
    out = pd.DataFrame(0.0, index=snapshots, columns=list(map(str, provinces)), dtype=float)

    # 1) Thermal generators on province/fuel buses.
    if hasattr(n, "generators") and not n.generators.empty and hasattr(n, "generators_t") and hasattr(n.generators_t, "p"):
        gen = n.generators
        car = gen["carrier"].astype(str).map(lambda c: _generator_selected(c, generator_carriers))
        gen_th = gen.index[car]
        if len(gen_th):
            gp = n.generators_t.p.reindex(index=snapshots, columns=gen_th).fillna(0.0)
            for g in gen_th:
                p = _resolve_bus_province(str(gen.at[g, "bus"]), prov_set)
                if p is not None:
                    out[p] = out[p].add(pd.to_numeric(gp[g], errors="coerce").fillna(0.0).clip(lower=0.0), fill_value=0.0)

    # 2) Thermal conversion links injecting into AC province buses.
    if hasattr(n, "links") and not n.links.empty and hasattr(n, "links_t") and hasattr(n.links_t, "p1"):
        links = n.links
        car = links.apply(lambda r: _link_selected(n, r, link_carrier_to_bus1_carrier), axis=1)
        l_th = links.index[car]
        if len(l_th):
            p1 = n.links_t.p1.reindex(index=snapshots, columns=l_th).fillna(0.0)
            for l in l_th:
                p = _resolve_bus_province(str(links.at[l, "bus1"]), prov_set)
                if p is None:
                    continue
                # PyPSA sign convention: injection at bus1 is -p1.
                inj = (-pd.to_numeric(p1[l], errors="coerce").fillna(0.0)).clip(lower=0.0)
                out[p] = out[p].add(inj, fill_value=0.0)

    return out.fillna(0.0)


def _province_offer_blocks(
    n: pypsa.Network,
    provinces: pd.Index,
    *,
    generator_carriers: set[str],
    link_carrier_to_bus1_carrier: dict[str, str],
) -> dict[str, list[tuple[float, float]]]:
    prov_set = set(map(str, provinces))
    blocks: dict[str, list[tuple[float, float]]] = {str(p): [] for p in provinces}

    if hasattr(n, "generators") and not n.generators.empty:
        for g, row in n.generators.iterrows():
            if not _generator_selected(row.get("carrier", ""), generator_carriers):
                continue
            p = _resolve_bus_province(str(row.get("bus", "")), prov_set)
            if p is None:
                continue
            cap = float(pd.to_numeric(row.get("p_nom", 0.0), errors="coerce") or 0.0)
            mc = float(pd.to_numeric(row.get("marginal_cost", 0.0), errors="coerce") or 0.0)
            if cap > 0:
                blocks[p].append((cap, mc))

    if hasattr(n, "links") and not n.links.empty:
        for l, row in n.links.iterrows():
            if not _link_selected(n, row, link_carrier_to_bus1_carrier):
                continue
            p = _resolve_bus_province(str(row.get("bus1", "")), prov_set)
            if p is None:
                continue
            p_nom = float(pd.to_numeric(row.get("p_nom", 0.0), errors="coerce") or 0.0)
            eta = float(pd.to_numeric(row.get("efficiency", 1.0), errors="coerce") or 1.0)
            cap = p_nom * max(eta, 0.0)
            mc = float(pd.to_numeric(row.get("marginal_cost", 0.0), errors="coerce") or 0.0)
            if cap > 0:
                blocks[p].append((cap, mc))

    return blocks


def _build_interp_curve(blocks: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """
    Piecewise-linear mapped price vs. thermal load ratio.

    Blocks are sorted by marginal cost (merit order). Cumulative capacity fractions
    run to 1. The first segment starts at (load ratio 0, price 0); then between
    consecutive cumulative boundaries, price varies linearly to the marginal cost
    of the corresponding merit-order block (continuous piecewise linear).
    """
    if not blocks:
        # Fallback curve aligned with README example (EUR/MWh).
        x = np.array([0.0, 0.50, 0.70, 0.85, 0.95, 1.00], dtype=float)
        y = np.array([0.0, 45.0, 55.0, 75.0, 192.0, 192.0], dtype=float)
        return x, y
    blocks_sorted = sorted(blocks, key=lambda t: float(t[1]))
    caps = np.array([max(float(c), 0.0) for c, _ in blocks_sorted], dtype=float)
    prices = np.array([float(p) for _, p in blocks_sorted], dtype=float)
    cap_sum = float(caps.sum())
    if cap_sum <= 0:
        pmax = float(np.max(prices)) if prices.size else 0.0
        return np.array([0.0, 1.0], dtype=float), np.array([0.0, max(pmax, 0.0)], dtype=float)
    cum = np.clip(np.cumsum(caps) / cap_sum, 0.0, 1.0)
    # Knots: (0, 0), (cum_0, mc_1), (cum_1, mc_2), …, (cum_{n-1}, mc_n).
    x = np.concatenate(([0.0], cum))
    y = np.concatenate(([0.0], prices))
    # Strictly increasing xp for np.interp: merge duplicate cumulative shares (last y wins).
    x_list: list[float] = []
    y_list: list[float] = []
    for xi, yi in zip(x.tolist(), y.tolist()):
        yi = max(float(yi), 0.0)
        if x_list and xi <= x_list[-1] + 1e-15:
            x_list[-1] = float(xi)
            y_list[-1] = yi
        else:
            x_list.append(float(xi))
            y_list.append(yi)
    return np.asarray(x_list, dtype=float), np.asarray(y_list, dtype=float)


def _local_mapped_prices(
    n: pypsa.Network,
    week_freq: str,
    *,
    generator_carriers: set[str],
    link_carrier_to_bus1_carrier: dict[str, str],
    config_path: str | Path | None = None,
) -> pd.DataFrame:
    provinces = _province_elec_buses(n)
    snapshots = pd.Index(n.snapshots)
    thermal = _infer_local_thermal_dispatch(
        n,
        provinces,
        snapshots,
        generator_carriers=generator_carriers,
        link_carrier_to_bus1_carrier=link_carrier_to_bus1_carrier,
    )
    blocks = _province_offer_blocks(
        n,
        provinces,
        generator_carriers=generator_carriers,
        link_carrier_to_bus1_carrier=link_carrier_to_bus1_carrier,
    )
    supply_settings = _load_mapped_supply_curve_settings(config_path=config_path)
    cfg_curve = _load_mapped_price_control_points(config_path=config_path) if supply_settings is None else None
    out = pd.DataFrame(index=snapshots, columns=list(map(str, provinces)), dtype=float)

    # Two-step load-ratio construction:
    # 1) Base load ratio = actual output / total installed capacity.
    # 2) Monthly-max adjustment = divide by peak load ratio within each calendar month
    #    (per province, using the same snapshot month on the index).
    # This preserves intra-month shape while anchoring the bid curve to that month’s peak utilisation.
    cap_by_province = pd.Series(
        {p: float(sum(cap for cap, _ in blocks.get(p, []))) for p in out.columns},
        dtype=float,
    )
    cap_by_province = cap_by_province.where(cap_by_province > 0.0, np.nan)
    lr_base = thermal.divide(cap_by_province, axis=1).clip(lower=0.0, upper=1.0)
    # Keep `week_freq` for backward-compatible interfaces; normalization uses monthly peaks only.
    _ = week_freq
    idx = lr_base.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise TypeError(
            "mapped load-ratio monthly normalization requires a DatetimeIndex on network snapshots; "
            f"got {type(idx).__name__}"
        )
    month_key = idx.to_period("M")
    lr_month_max = lr_base.groupby(month_key).transform("max")
    lr_month_max = lr_month_max.where(lr_month_max > 0.0, np.nan)
    lr = lr_base.divide(lr_month_max).clip(lower=0.0, upper=1.0).fillna(0.0)

    for p in out.columns:
        if supply_settings is not None:
            fuel_ref = _province_ref_fuel_eur_from_seg0_network(
                n,
                p,
                generator_carriers=generator_carriers,
                link_carrier_to_bus1_carrier=link_carrier_to_bus1_carrier,
            )
            if fuel_ref is None or fuel_ref <= 0.0:
                fuel_ref = _province_ref_fuel_eur_from_blocks(blocks.get(p, []) or [])
            if fuel_ref is not None and float(fuel_ref) > 0.0:
                mult = _mapped_multiplier_from_lr_normalized(lr[p].to_numpy(dtype=float), supply_settings)
                out[p] = (mult * float(fuel_ref)).astype(float)
                continue
        if cfg_curve is not None:
            x, y = cfg_curve
        else:
            x, y = _build_interp_curve(blocks.get(p, []))
        out[p] = np.interp(lr[p].to_numpy(dtype=float), x, y).astype(float)

    return out.fillna(0.0).clip(lower=0.0)


def _is_uncongested(link_row: pd.Series, p0_t: pd.Series, eps_mw: float) -> pd.Series:
    p_nom = float(pd.to_numeric(link_row.get("p_nom", 0.0), errors="coerce") or 0.0)
    if p_nom <= 0:
        return pd.Series(False, index=p0_t.index)
    loading = pd.to_numeric(p0_t, errors="coerce").abs()
    return (loading <= max(p_nom - float(eps_mw), 0.0)).fillna(False)


def _province_marginal_prices(
    n: pypsa.Network,
    provinces: pd.Index,
    snapshots: pd.Index,
) -> pd.DataFrame:
    cols = list(map(str, provinces))
    out = pd.DataFrame(0.0, index=snapshots, columns=cols, dtype=float)
    if not hasattr(n, "buses_t") or not hasattr(n.buses_t, "marginal_price"):
        return out
    mp = n.buses_t.marginal_price.reindex(index=snapshots, columns=cols)
    mp = mp.apply(pd.to_numeric, errors="coerce").fillna(0.0).astype(float)
    return out.add(mp, fill_value=0.0)


def _apply_cross_border_exports(
    n: pypsa.Network,
    local_prices: pd.DataFrame,
    marginal_prices: pd.DataFrame,
    *,
    import_agg: str,
    line_cong_eps_mw: float,
    min_inflow_mw: float,
) -> pd.DataFrame:
    provinces = list(local_prices.columns)
    prov_set = set(provinces)
    local = local_prices.copy().astype(float)
    marginal = marginal_prices.reindex(index=local.index, columns=local.columns).fillna(0.0).astype(float)
    export_price_candidates: dict[str, list[pd.Series]] = {p: [] for p in provinces}

    if not hasattr(n, "links") or n.links.empty or not hasattr(n, "links_t") or not hasattr(n.links_t, "p0"):
        return local

    for l, row in n.links.iterrows():
        b0 = str(row.get("bus0", ""))
        b1 = str(row.get("bus1", ""))
        if b0 not in prov_set or b1 not in prov_set:
            continue

        p0 = _safe_series(n.links_t.p0, str(l), local.index)
        uncong = _is_uncongested(row, p0, float(line_cong_eps_mw))
        if not uncong.any():
            continue

        eta_fwd = float(pd.to_numeric(row.get("efficiency", 1.0), errors="coerce") or 1.0)
        eta_rev = float(pd.to_numeric(row.get("efficiency2", np.nan), errors="coerce"))
        if np.isnan(eta_rev):
            eta_rev = eta_fwd
        eta_fwd = max(eta_fwd, 1e-6)
        eta_rev = max(eta_rev, 1e-6)

        # Forward flow: b0 -> b1 if p0 > 0.
        # Exporting province b0 references receiving province b1 price,
        # mapped back with distance/loss correction.
        fwd_mask = uncong & (p0 > float(min_inflow_mw))
        if fwd_mask.any():
            ref_fwd = (marginal[b1] * eta_fwd).where(fwd_mask)
            export_price_candidates[b0].append(ref_fwd)

        # Reverse flow: b1 -> b0 if p0 < 0.
        rev_mask = uncong & (p0 < -float(min_inflow_mw))
        if rev_mask.any():
            ref_rev = (marginal[b0] * eta_rev).where(rev_mask)
            export_price_candidates[b1].append(ref_rev)

    out = local.copy()
    for p in provinces:
        if not export_price_candidates[p]:
            continue
        mat = pd.concat(export_price_candidates[p], axis=1)
        # For exporting province, use the highest receiving-side reference price.
        # Keep import_agg argument for backward-compatible interfaces.
        _ = import_agg
        agg_ref = mat.max(axis=1, skipna=True)
        out[p] = np.maximum(local[p], agg_ref.fillna(local[p]))

    return out.fillna(0.0).clip(lower=0.0)


def mapped_retail_prices(
    n: pypsa.Network,
    *,
    week_freq: str,
    import_agg: str,
    line_cong_eps_mw: float,
    min_inflow_mw: float,
    config_path: str | Path | None = None,
) -> pd.DataFrame:
    generator_carriers, link_carrier_to_bus1_carrier = _load_mapped_carrier_config(config_path=config_path)
    local = _local_mapped_prices(
        n,
        week_freq=str(week_freq),
        generator_carriers=generator_carriers,
        link_carrier_to_bus1_carrier=link_carrier_to_bus1_carrier,
        config_path=config_path,
    )
    marginal = _province_marginal_prices(
        n,
        provinces=pd.Index(local.columns),
        snapshots=pd.Index(local.index),
    )
    return _apply_cross_border_exports(
        n,
        local,
        marginal,
        import_agg=str(import_agg),
        line_cong_eps_mw=float(line_cong_eps_mw),
        min_inflow_mw=float(min_inflow_mw),
    )


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
    baseline_network_path: str | None = None,
    out_csv: str,
    provinces: list[str] | None,
    week_freq: str,
    import_agg: str,
    line_cong_eps_mw: float,
    min_inflow_mw: float,
    price_mode: str = "marginal",
    calibrate_with_baseline_max: bool = True,
    currency: str = "EUR",
    fx_cny_per_eur: float = 7.8,
    plot_shandong_price_thermal: bool = True,
    shandong_plot_prefix: str | None = None,
    shandong_plot_sample: int = 0,
    shandong_seasonal_random_day_seed: int | None = 42,
    config_path: str | None = None,
) -> None:
    n = pypsa.Network(network_path)
    # Parameters below are used by mapped sidecar reconstruction.
    _ = (week_freq, import_agg, line_cong_eps_mw, min_inflow_mw)
    cfg = ReconstructPriceConfig(week_freq=week_freq)
    if price_mode == "marginal":
        prices = marginal_retail_prices(n, config=cfg)
    else:
        raise ValueError("price_mode must be 'marginal'")
    prices = _select_provinces(prices, provinces)

    nodal_marginal = _all_bus_marginal_prices(n)
    nodal_marginal = nodal_marginal.reindex(index=prices.index)
    nodal_marginal = _select_nodal_marginal(nodal_marginal, provinces)

    # New sidecar output: mapped prices reconstructed from dispatch result.
    mapped_prices = mapped_retail_prices(
        n,
        week_freq=week_freq,
        import_agg=import_agg,
        line_cong_eps_mw=line_cong_eps_mw,
        min_inflow_mw=min_inflow_mw,
        config_path=config_path,
    )
    mapped_prices = _select_provinces(mapped_prices, provinces)

    if calibrate_with_baseline_max:
        if not baseline_network_path:
            raise ValueError("calibrate_with_baseline_max=True requires baseline_network_path.")
        n0 = pypsa.Network(baseline_network_path)
        baseline = marginal_retail_prices(n0, config=cfg)
        baseline = _select_provinces(baseline, provinces)
        baseline = baseline.reindex(index=prices.index, columns=prices.columns).fillna(0.0).astype(float)
        prices_f = prices.astype(float)
        prices = prices_f.mask(prices_f < baseline, baseline)
        mapped_f = mapped_prices.astype(float)
        mapped_prices = mapped_f.mask(mapped_f < baseline, baseline)
        nodal_marginal = _calibrate_nodal_with_baseline(nodal_marginal, n0)

    cur = str(currency).upper()
    if cur in {"CNY", "RMB"}:
        fx = float(fx_cny_per_eur)
        prices = prices.astype(float) * fx
        mapped_prices = mapped_prices.astype(float) * fx
        nodal_marginal = nodal_marginal.astype(float) * fx
    elif cur in {"EUR"}:
        prices = prices.astype(float)
        mapped_prices = mapped_prices.astype(float)
        nodal_marginal = nodal_marginal.astype(float)
    else:
        raise ValueError("currency must be EUR or CNY (RMB accepted as alias)")

    out_path = Path(out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prices.to_csv(out_path, index_label="snapshot")
    mapped_out_path = out_path.with_name(f"{out_path.stem}_mapped{out_path.suffix}")
    mapped_prices.to_csv(mapped_out_path, index_label="snapshot")
    nodal_out_path = out_path.with_name(f"{out_path.stem}_nodal_marginal{out_path.suffix}")
    nodal_marginal.to_csv(nodal_out_path, index_label="snapshot")

    if plot_shandong_price_thermal:
        if shandong_plot_prefix:
            plot_prefix = Path(shandong_plot_prefix)
        else:
            plot_prefix = out_path.parent / "plots" / f"shandong_price_vs_thermal_{out_path.stem}"
        shandong_mapped_price = mapped_prices["Shandong"] if "Shandong" in mapped_prices.columns else None
        export_price_vs_thermal_plots(
            n=n,
            out_prefix=plot_prefix,
            province="Shandong",
            week_freq=str(week_freq),
            sample=int(shandong_plot_sample),
            price_mode=str(price_mode),
            currency=str(currency),
            fx_cny_per_eur=float(fx_cny_per_eur),
            price_series=shandong_mapped_price,
            price_label="Mapped price",
        )
        if shandong_mapped_price is not None:
            export_seasonal_random_day_profiles(
                n=n,
                out_prefix=plot_prefix,
                province="Shandong",
                currency=str(currency),
                config_path=(Path(config_path) if config_path else None),
                price_series=shandong_mapped_price,
                price_label="Mapped price",
                random_state=shandong_seasonal_random_day_seed,
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", required=True, help="Solved postnetwork .nc path")
    ap.add_argument(
        "--baseline-network",
        default=None,
        help="Optional baseline/planning .nc used for price calibration (see --calibrate-max-with-baseline).",
    )
    ap.add_argument("--out", required=True, help="Output CSV path")
    ap.add_argument("--province", action="append", default=None, help="Province to include (repeatable). If omitted, export all.")
    ap.add_argument(
        "--week-freq",
        default="SM",
        help=(
            "Deprecated compatibility argument. "
            "Mapped load-ratio denominator uses each calendar month's maximum thermal load ratio."
        ),
    )
    ap.add_argument(
        "--import-agg",
        default="min_offer",
        choices=["min_offer", "max_offer"],
        help=(
            "Deprecated compatibility argument. "
            "Current mapped export adjustment always uses highest receiving-side reference."
        ),
    )
    ap.add_argument("--line-cong-eps-mw", type=float, default=1e-3, help="Congestion slack in MW (default: 1e-3)")
    ap.add_argument("--min-inflow-mw", type=float, default=1e-3, help="Ignore smaller line flows (default: 1e-3)")
    ap.add_argument(
        "--price-mode",
        default="marginal",
        choices=["marginal"],
        help=(
            "Primary output mode. marginal: buses_t.marginal_price."
        ),
    )
    ap.add_argument(
        "--calibrate-max-with-baseline",
        action="store_true",
        help=(
            "When exporting, take elementwise max between dispatch LMPs (--network) and "
            "baseline LMPs (--baseline-network)."
        ),
    )
    ap.add_argument(
        "--currency",
        default="CNY",
        choices=["EUR", "CNY", "RMB"],
        help="Output currency unit for prices (default: CNY).",
    )
    ap.add_argument(
        "--fx-cny-per-eur",
        type=float,
        default=7.8,
        help="FX rate used when --currency CNY/RMB (default: 7.8).",
    )
    ap.add_argument(
        "--skip-shandong-plot",
        action="store_true",
        help="Skip exporting Shandong price-vs-thermal scatter/time-series figures.",
    )
    ap.add_argument(
        "--shandong-plot-prefix",
        default=None,
        help=(
            "Optional output prefix (without extension) for Shandong plot artifacts. "
            "Default: <out_dir>/plots/shandong_price_vs_thermal_<out_stem>"
        ),
    )
    ap.add_argument(
        "--config",
        default=None,
        help="Optional config.yaml path used for mapped carrier selection.",
    )
    ap.add_argument(
        "--shandong-plot-sample",
        type=int,
        default=0,
        help="Optional scatter downsample N points for Shandong plot (0=all).",
    )
    ap.add_argument(
        "--shandong-seasonal-day-seed",
        type=int,
        default=42,
        help=(
            "RNG seed for picking one random day per season for Shandong mapped price / thermal subplot; "
            "use a negative value for nondeterministic choice."
        ),
    )
    args = ap.parse_args()

    export_prices(
        network_path=args.network,
        baseline_network_path=args.baseline_network,
        out_csv=args.out,
        provinces=args.province,
        week_freq=str(args.week_freq),
        import_agg=str(args.import_agg),
        line_cong_eps_mw=float(args.line_cong_eps_mw),
        min_inflow_mw=float(args.min_inflow_mw),
        price_mode=str(args.price_mode),
        calibrate_with_baseline_max=bool(args.calibrate_max_with_baseline),
        currency=str(args.currency),
        fx_cny_per_eur=float(args.fx_cny_per_eur),
        plot_shandong_price_thermal=(not bool(args.skip_shandong_plot)),
        shandong_plot_prefix=(str(args.shandong_plot_prefix) if args.shandong_plot_prefix else None),
        shandong_plot_sample=int(args.shandong_plot_sample),
        shandong_seasonal_random_day_seed=(
            int(args.shandong_seasonal_day_seed)
            if int(args.shandong_seasonal_day_seed) >= 0
            else None
        ),
        config_path=(str(args.config) if args.config else None),
    )


if __name__ == "__main__":
    main()

