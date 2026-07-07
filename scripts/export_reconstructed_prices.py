"""
Export reconstructed electricity prices from a solved PyPSA network `.nc`.

This is intended to be called from Snakemake (preferred) or from CLI.

Output CSVs (same snapshot index, selected provinces unless --province is omitted for nodal):
- Primary: marginal (provincial) prices from `buses_t.marginal_price`
- Sidecar: mapped reconstruction from segmented thermal bids
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
    - lr_threshold_first: load ratio (after weekly norm) used as first piece boundary
    - mult_at_bandwidth_start: mult at and below lr_threshold_first (typically 1.0 = 100% fuel)
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
    out[mask_le] = m_start
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


def _national_coal_power_1x_eur_from_seg0_network(n: pypsa.Network) -> float:
    """Capacity-weighted 1x coal-power cost from ``coal power plant__seg0`` blocks."""
    if not hasattr(n, "generators") or n.generators.empty:
        return 0.0
    wsum = 0.0
    capsum = 0.0
    for g, row in n.generators.iterrows():
        if not str(g).endswith("__seg0"):
            continue
        if str(row.get("carrier", "")) != "coal power plant":
            continue
        cap = float(pd.to_numeric(row.get("p_nom", 0.0), errors="coerce") or 0.0)
        mc = float(pd.to_numeric(row.get("marginal_cost", 0.0), errors="coerce") or 0.0)
        if cap > 1e-9 and mc >= 0.0:
            wsum += cap * mc
            capsum += cap
    return float(wsum / capsum) if capsum > 1e-9 else 0.0


def _province_coal_power_1x_prices(
    n: pypsa.Network,
    provinces: pd.Index,
    snapshots: pd.Index,
) -> pd.DataFrame:
    """Province-hour reference price equal to 1x coal-power generation cost."""
    province_cols = list(map(str, provinces))
    fallback = _national_coal_power_1x_eur_from_seg0_network(n)
    out = pd.DataFrame(index=snapshots, columns=province_cols, dtype=float)
    for p in province_cols:
        val = _province_ref_fuel_eur_from_seg0_network(
            n,
            p,
            generator_carriers={"coal power plant"},
            link_carrier_to_bus1_carrier={},
        )
        if val is None or val <= 0.0:
            val = fallback
        out[p] = float(val or 0.0)
    return out.fillna(0.0).clip(lower=0.0)


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


def _load_heating_season_chp_exclusion_config(
    config_path: str | Path | None = None,
) -> tuple[bool, int, int, int, int]:
    """
    Heating season when CHP is excluded from mapped load-ratio (coal/gas stack without CHP).

    Returns (enabled, start_month, start_day, end_month, end_day). Default: Nov 15–Mar 15 inclusive, enabled.

    YAML (optional):
      dispatch_segmented_prices.price_export.heating_season_chp_excluded_from_mapped_lr:
        enabled: true
        start: [11, 15]
        end: [3, 15]
    """
    default = (True, 11, 15, 3, 15)
    cfg_path = Path(config_path) if config_path is not None else _default_config_path()
    if not cfg_path.exists():
        return default
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    dsp = cfg.get("dispatch_segmented_prices", {}) or {}
    pe = dsp.get("price_export", {}) or {}
    h = (
        pe.get("heating_season_chp_excluded_from_mapped_lr")
        or pe.get("heating_season_mapped_lr_exclude_chp")
    )
    if not isinstance(h, dict):
        return default
    if h.get("enabled") is False:
        return (False, 11, 15, 3, 15)
    s = h.get("start", [11, 15])
    e = h.get("end", [3, 15])
    if (
        not isinstance(s, (list, tuple))
        or not isinstance(e, (list, tuple))
        or len(s) != 2
        or len(e) != 2
    ):
        return default
    sm, sd = int(s[0]), int(s[1])
    em, ed = int(e[0]), int(e[1])
    return (True, sm, sd, em, ed)


def _heating_season_mask(idx: pd.DatetimeIndex, sm: int, sd: int, em: int, ed: int) -> pd.Series:
    """Boolean Series aligned to idx: inclusive date range spanning year boundary if sm > em."""

    def _one(ts: pd.Timestamp) -> bool:
        m, d = int(ts.month), int(ts.day)
        after_start = (m, d) >= (sm, sd)
        before_end = (m, d) <= (em, ed)
        if sm > em:
            return bool(after_start or before_end)
        return bool((sm, sd) <= (m, d) <= (em, ed))

    return pd.Series([_one(pd.Timestamp(x)) for x in idx], index=idx, dtype=bool)


def _load_thermal_load_floor_config(config_path: str | Path | None = None) -> tuple[bool, float]:
    """
    Optional mapped-price-only must-run threshold:

      dispatch_segmented_prices.price_export.thermal_load_floor:
        enabled: true
        ratio: 0.10

    Province-hours at or near ratio * local AC electricity load are treated as
    zero-price must-run hours instead of thermal-load-ratio mapped prices.
    """
    cfg_path = Path(config_path) if config_path is not None else _default_config_path()
    if not cfg_path.exists():
        return (False, 0.0)
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    dsp = cfg.get("dispatch_segmented_prices", {}) or {}
    pe = dsp.get("price_export", {}) or {}
    floor_cfg = pe.get("thermal_load_floor") or {}
    if not isinstance(floor_cfg, dict) or floor_cfg.get("enabled") is False:
        return (False, 0.0)
    ratio = float(floor_cfg.get("ratio", 0.0) or 0.0)
    if ratio <= 0.0:
        return (False, 0.0)
    if ratio >= 1.0:
        raise ValueError(
            "dispatch_segmented_prices.price_export.thermal_load_floor.ratio must be < 1"
        )
    return (True, ratio)


def _load_mapped_price_source_config(config_path: str | Path | None = None) -> str:
    """
    Select the mapped sidecar source.

    Config:
      dispatch_segmented_prices.price_export.mapped_price_source:
        reconstructed                  (default; current mapped reconstruction)
        planning_marginal_floor_zero   (planning marginal prices, zeroed at thermal floor)
    """
    cfg_path = Path(config_path) if config_path is not None else _default_config_path()
    if not cfg_path.exists():
        return "reconstructed"
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    dsp = cfg.get("dispatch_segmented_prices", {}) or {}
    pe = dsp.get("price_export", {}) or {}
    source = str(pe.get("mapped_price_source", "reconstructed") or "reconstructed").strip().lower()
    aliases = {
        "local_reconstructed": "reconstructed",
        "mapped_reconstructed": "reconstructed",
        "planning": "planning_marginal_floor_zero",
        "planning_marginal": "planning_marginal_floor_zero",
        "planning_with_floor_zero": "planning_marginal_floor_zero",
    }
    source = aliases.get(source, source)
    if source not in {"reconstructed", "planning_marginal_floor_zero"}:
        raise ValueError(
            "dispatch_segmented_prices.price_export.mapped_price_source must be "
            "'reconstructed' or 'planning_marginal_floor_zero'"
        )
    return source


def _load_apply_synchronous_generation_floor_zero_mask(config_path: str | Path | None = None) -> bool:
    """
    Whether planning-marginal floor-zero mapped prices also zero out synchronous-generation
    floor hours. Default True for backward compatibility with the main workflow.
    """
    cfg_path = Path(config_path) if config_path is not None else _default_config_path()
    if not cfg_path.exists():
        return True
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    pe = ((cfg.get("dispatch_segmented_prices") or {}).get("price_export") or {})
    if "apply_synchronous_generation_floor_zero_mask" in pe:
        return bool(pe.get("apply_synchronous_generation_floor_zero_mask"))
    return True


def _load_low_output_carrier_scope(config_path: str | Path | None = None) -> str:
    """
    Carrier scope for the low-output zero-price mask.

    Config:
      dispatch_segmented_prices.price_export.low_output_carrier_scope:
        mapped_carriers               (default; legacy behavior)
        synchronous_generation_floor  (use the synchronous floor carrier set)
    """
    cfg_path = Path(config_path) if config_path is not None else _default_config_path()
    if not cfg_path.exists():
        return "mapped_carriers"
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    pe = ((cfg.get("dispatch_segmented_prices") or {}).get("price_export") or {})
    scope = str(pe.get("low_output_carrier_scope", "mapped_carriers") or "mapped_carriers")
    scope = scope.strip().lower().replace("-", "_")
    aliases = {
        "mapped": "mapped_carriers",
        "thermal": "mapped_carriers",
        "sync": "synchronous_generation_floor",
        "synchronous": "synchronous_generation_floor",
        "sync_floor": "synchronous_generation_floor",
    }
    scope = aliases.get(scope, scope)
    if scope not in {"mapped_carriers", "synchronous_generation_floor"}:
        raise ValueError(
            "dispatch_segmented_prices.price_export.low_output_carrier_scope must be "
            "'mapped_carriers' or 'synchronous_generation_floor'"
        )
    return scope


def _load_synchronous_generation_floor_config(
    config_path: str | Path | None = None,
) -> tuple[bool, float, set[str], dict[str, str], float, int, int]:
    """
    Load the planning/dispatch synchronous-generation floor config.

    Config:
      synchronous_generation_floor:
        enabled: true
        ratio: 0.10
        Generator: [...]
        Link: {carrier: {only_bus1_carrier: "AC"}}
      dispatch_segmented_prices.sync_floor_slack_mw: 1.0
    """
    cfg_path = Path(config_path) if config_path is not None else _default_config_path()
    if not cfg_path.exists():
        return (False, 0.0, set(), {}, 1.0, 2025, 2050)
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    floor_cfg = cfg.get("synchronous_generation_floor", {}) or {}
    if not isinstance(floor_cfg, dict) or floor_cfg.get("enabled") is False:
        return (False, 0.0, set(), {}, 1.0, 2025, 2050)
    ratio = float(floor_cfg.get("ratio", 0.0) or 0.0)
    if ratio <= 0.0:
        return (False, 0.0, set(), {}, 1.0, 2025, 2050)
    if ratio >= 1.0:
        raise ValueError("synchronous_generation_floor.ratio must be < 1")

    gen_raw = floor_cfg.get("Generator", []) or []
    if isinstance(gen_raw, dict):
        generator_carriers = {str(k) for k in gen_raw.keys()}
    elif isinstance(gen_raw, (list, tuple, set)):
        generator_carriers = {str(v) for v in gen_raw}
    else:
        raise ValueError("synchronous_generation_floor.Generator must be a list or mapping")

    link_raw = floor_cfg.get("Link", {}) or {}
    if not isinstance(link_raw, dict):
        raise ValueError("synchronous_generation_floor.Link must be a mapping")
    link_carrier_to_bus1_carrier: dict[str, str] = {}
    for k, v in link_raw.items():
        only_bus1 = ""
        if isinstance(v, dict):
            only_bus1 = str(v.get("only_bus1_carrier", "") or "")
        link_carrier_to_bus1_carrier[str(k)] = only_bus1

    dsp = cfg.get("dispatch_segmented_prices", {}) or {}
    slack_mw = float(dsp.get("sync_floor_slack_mw", 1.0) or 0.0)
    apply_start_year = int(floor_cfg.get("apply_start_year", 2025))
    apply_end_year = int(floor_cfg.get("apply_end_year", 2050))
    return (True, ratio, generator_carriers, link_carrier_to_bus1_carrier, slack_mw, apply_start_year, apply_end_year)


def _load_sync_floor_zero_band_mw(config_path: str | Path | None = None) -> float:
    """
    Tolerance above the model synchronous-generation floor RHS for zero-price masking.

    Config:
      dispatch_segmented_prices.price_export.sync_floor_zero_band_mw
    """
    cfg_path = Path(config_path) if config_path is not None else _default_config_path()
    if not cfg_path.exists():
        return 1.0
    with cfg_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    pe = ((cfg.get("dispatch_segmented_prices") or {}).get("price_export") or {})
    return max(float(pe.get("sync_floor_zero_band_mw", 1.0) or 0.0), 0.0)


def _sync_generation_floor_zero_mask(
    sync_output: pd.DataFrame,
    load_mw: pd.DataFrame,
    *,
    ratio: float,
    rhs_slack_mw: float,
    zero_band_mw: float,
    apply_start_year: int,
    apply_end_year: int,
) -> pd.DataFrame:
    """
    Zero-price mask aligned with the dispatch synchronous-generation floor constraint.

    Model RHS per province-hour: max(ratio * local AC load - rhs_slack_mw, 0).
    Mask hours where synchronous output is at or just above that minimum (must-run).
    """
    min_required = (load_mw.astype(float) * float(ratio) - float(rhs_slack_mw)).clip(lower=0.0)
    min_required = min_required.reindex(index=sync_output.index, columns=sync_output.columns).fillna(0.0)
    mask = sync_output.astype(float) <= (min_required + float(zero_band_mw))
    years = pd.Series(pd.DatetimeIndex(sync_output.index).year, index=sync_output.index, dtype=int)
    active = (years >= int(apply_start_year)) & (years <= int(apply_end_year))
    return mask & active.to_numpy(dtype=bool)[:, None]


def _province_ac_load(
    n: pypsa.Network,
    provinces: pd.Index,
    snapshots: pd.Index,
) -> pd.DataFrame:
    province_cols = list(map(str, provinces))
    province_set = set(province_cols)
    out = pd.DataFrame(0.0, index=snapshots, columns=province_cols, dtype=float)
    if not hasattr(n, "loads") or n.loads.empty or not hasattr(n, "loads_t"):
        return out
    load_values = n.loads_t.p_set if hasattr(n.loads_t, "p_set") else n.loads_t.p
    load_values = load_values.reindex(index=snapshots).fillna(0.0)
    for load_name, row in n.loads.iterrows():
        if load_name not in load_values.columns:
            continue
        bus = str(row.get("bus", ""))
        if bus not in province_set:
            continue
        out[bus] = out[bus].add(
            pd.to_numeric(load_values[load_name], errors="coerce").fillna(0.0).clip(lower=0.0),
            fill_value=0.0,
        )
    return out


def _thermal_load_floor_mask(
    thermal: pd.DataFrame,
    n: pypsa.Network,
    provinces: pd.Index,
    snapshots: pd.Index,
    ratio: float,
) -> pd.DataFrame:
    load_floor = _province_ac_load(n, provinces, snapshots) * float(ratio)
    load_floor = load_floor.reindex(index=thermal.index, columns=thermal.columns).fillna(0.0)
    floor_tolerance_mw = 1.0
    return thermal.astype(float) <= (load_floor + floor_tolerance_mw)


def _thermal_load_floor_band_mask(
    thermal: pd.DataFrame,
    n: pypsa.Network,
    provinces: pd.Index,
    snapshots: pd.Index,
    ratio: float,
    multiplier: float,
) -> pd.DataFrame:
    load_floor_band = _province_ac_load(n, provinces, snapshots) * float(ratio) * float(multiplier)
    load_floor_band = load_floor_band.reindex(index=thermal.index, columns=thermal.columns).fillna(0.0)
    floor_tolerance_mw = 1.0
    return thermal.astype(float) <= (load_floor_band + floor_tolerance_mw)


def _province_ref_fuel_prices(
    n: pypsa.Network,
    provinces: pd.Index,
    snapshots: pd.Index,
    *,
    generator_carriers: set[str],
    link_carrier_to_bus1_carrier: dict[str, str],
    week_freq: str,
    config_path: str | Path | None = None,
) -> pd.DataFrame:
    province_cols = list(map(str, provinces))
    out = pd.DataFrame(index=snapshots, columns=province_cols, dtype=float)

    _, _, blocks_full = _weekly_lr_and_blocks(
        n,
        provinces,
        snapshots,
        week_freq,
        generator_carriers=generator_carriers,
        link_carrier_to_bus1_carrier=link_carrier_to_bus1_carrier,
        config_path=config_path,
    )

    for p in province_cols:
        fuel_f = _province_ref_fuel_eur_from_seg0_network(
            n,
            p,
            generator_carriers=generator_carriers,
            link_carrier_to_bus1_carrier=link_carrier_to_bus1_carrier,
        )
        if fuel_f is None or fuel_f <= 0.0:
            fuel_f = _province_ref_fuel_eur_from_blocks(blocks_full.get(p, []) or [])
        fuel_f = 0.0 if fuel_f is None else float(fuel_f)
        out[p] = fuel_f

    return out.fillna(0.0).clip(lower=0.0)


def _weekly_normalized_lr(
    thermal: pd.DataFrame,
    blocks: dict[str, list[tuple[float, float]]],
    province_cols: list[str],
    week_freq: str,
    min_output_ratio: float = 0.4,
) -> pd.DataFrame:
    cap_by_province = pd.Series(
        {p: float(sum(cap for cap, _ in blocks.get(p, []))) for p in province_cols},
        dtype=float,
    )
    cap_by_province = cap_by_province.where(cap_by_province > 0.0, np.nan)
    lr_base = thermal.reindex(columns=province_cols).divide(cap_by_province, axis=1).clip(
        lower=0.0, upper=1.0
    )
    idx = lr_base.index
    if not isinstance(idx, pd.DatetimeIndex):
        raise TypeError(
            "mapped load-ratio weekly normalization requires a DatetimeIndex on network snapshots; "
            f"got {type(idx).__name__}"
        )
    grouped = lr_base.groupby(pd.Grouper(freq=week_freq))
    lr_week_max = grouped.transform("max")
    floor_ratio = max(float(min_output_ratio), 1e-9)
    lr_week_min_floor_equiv = grouped.transform("min").clip(lower=0.0) / floor_ratio
    lr_denominator = lr_week_max.where(lr_week_max >= lr_week_min_floor_equiv, lr_week_min_floor_equiv)
    lr_denominator = lr_denominator.where(lr_denominator > 0.0, np.nan)
    return lr_base.divide(lr_denominator).clip(lower=0.0, upper=1.0).fillna(0.0)


def _weekly_lr_and_blocks(
    n: pypsa.Network,
    provinces: pd.Index,
    snapshots: pd.Index,
    week_freq: str,
    *,
    generator_carriers: set[str],
    link_carrier_to_bus1_carrier: dict[str, str],
    config_path: str | Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, list[tuple[float, float]]]]:
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
    cols = list(map(str, provinces))
    supply_settings = _load_mapped_supply_curve_settings(config_path=config_path)
    min_output_ratio = 0.4
    if supply_settings is not None:
        min_output_ratio = float(supply_settings.get("lr_threshold_first", min_output_ratio))
    lr = _weekly_normalized_lr(thermal, blocks, cols, week_freq, min_output_ratio=min_output_ratio)
    return thermal, lr, blocks


def _daily_low_output_zero_mask(
    thermal_series: pd.Series,
    threshold: float | pd.Series = 0.4,
    freq: str = "D",
    reserve_margin: float = 0.0,
) -> pd.Series:
    """Mask snapshots below threshold × grouped thermal maximum × reserve multiplier."""
    if not isinstance(thermal_series.index, pd.DatetimeIndex):
        raise TypeError(
            "low-output zeroing requires DatetimeIndex snapshots; "
            f"got {type(thermal_series.index).__name__}"
        )
    th = pd.to_numeric(thermal_series, errors="coerce").fillna(0.0).astype(float)
    group_max = th.groupby(pd.Grouper(freq=str(freq))).transform("max")
    if isinstance(threshold, pd.Series):
        thr = pd.to_numeric(threshold, errors="coerce").reindex(th.index).fillna(0.4).astype(float)
    else:
        thr = pd.Series(float(threshold), index=th.index, dtype=float)
    reserve_multiplier = 1.0 + max(float(reserve_margin), 0.0)
    cutoff = group_max * reserve_multiplier * thr
    return (th < cutoff) | (group_max <= 0.0)


def _daily_low_output_zero_threshold(snapshots: pd.Index, config_path: str | Path | None = None) -> pd.Series:
    """
    Daily low-output zeroing threshold.

    Config:
      dispatch_segmented_prices.price_export.daily_low_output_zero_threshold
      dispatch_segmented_prices.price_export.daily_low_output_zero_threshold_by_year

    Returns a per-snapshot threshold series so each year can be configured
    independently in a multi-year run.
    """
    idx = pd.DatetimeIndex(snapshots)
    default_threshold = 0.4
    by_year: dict[int, float] = {}

    cfg_path = Path(config_path) if config_path is not None else _default_config_path()
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        dsp = cfg.get("dispatch_segmented_prices", {}) or {}
        pe = dsp.get("price_export", {}) or {}

        if "daily_low_output_zero_threshold" in pe:
            default_threshold = float(pe.get("daily_low_output_zero_threshold", 0.4))

        by_year_raw = pe.get("daily_low_output_zero_threshold_by_year", {}) or {}
        if isinstance(by_year_raw, dict):
            for y_raw, v_raw in by_year_raw.items():
                try:
                    y = int(y_raw)
                    by_year[y] = float(v_raw)
                except (TypeError, ValueError):
                    continue

    out = pd.Series(default_threshold, index=idx, dtype=float)
    if by_year:
        years = pd.Series(idx.year, index=idx, dtype=int)
        for y, thr in by_year.items():
            out.loc[years == int(y)] = float(thr)
    return out.clip(lower=0.0, upper=1.0)


def _low_output_reference_freq(config_path: str | Path | None = None) -> str:
    """Frequency used for low-output reference maximum. Default keeps legacy daily behavior."""
    cfg_path = Path(config_path) if config_path is not None else _default_config_path()
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        pe = ((cfg.get("dispatch_segmented_prices") or {}).get("price_export") or {})
        return str(pe.get("low_output_reference_freq") or pe.get("daily_low_output_reference_freq") or "D")
    return "D"


def _low_output_reserve_margin(config_path: str | Path | None = None) -> float:
    """Reserve margin applied to the grouped thermal maximum before thresholding."""
    cfg_path = Path(config_path) if config_path is not None else _default_config_path()
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        pe = ((cfg.get("dispatch_segmented_prices") or {}).get("price_export") or {})
        return max(float(pe.get("low_output_reserve_margin", 0.0) or 0.0), 0.0)
    return 0.0


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
    supply_settings = _load_mapped_supply_curve_settings(config_path=config_path)
    cfg_curve = _load_mapped_price_control_points(config_path=config_path) if supply_settings is None else None
    out = pd.DataFrame(index=snapshots, columns=list(map(str, provinces)), dtype=float)

    thermal_full, lr_full, blocks_full = _weekly_lr_and_blocks(
        n,
        provinces,
        snapshots,
        week_freq,
        generator_carriers=generator_carriers,
        link_carrier_to_bus1_carrier=link_carrier_to_bus1_carrier,
        config_path=config_path,
    )
    low_output_thermal = thermal_full
    if _load_low_output_carrier_scope(config_path=config_path) == "synchronous_generation_floor":
        (
            floor_enabled,
            _floor_ratio,
            sync_generator_carriers,
            sync_link_carrier_to_bus1_carrier,
            _floor_slack_mw,
            _apply_start_year,
            _apply_end_year,
        ) = _load_synchronous_generation_floor_config(config_path=config_path)
        if floor_enabled:
            low_output_thermal = _infer_local_thermal_dispatch(
                n,
                provinces=provinces,
                snapshots=snapshots,
                generator_carriers=sync_generator_carriers,
                link_carrier_to_bus1_carrier=sync_link_carrier_to_bus1_carrier,
            )

    zero_threshold = _daily_low_output_zero_threshold(out.index, config_path=config_path)
    low_output_freq = _low_output_reference_freq(config_path=config_path)
    low_output_reserve_margin = _low_output_reserve_margin(config_path=config_path)

    for p in out.columns:
        th_active = pd.to_numeric(low_output_thermal[p], errors="coerce").fillna(0.0).astype(float)
        zero_mask = _daily_low_output_zero_mask(
            th_active,
            threshold=zero_threshold,
            freq=low_output_freq,
            reserve_margin=low_output_reserve_margin,
        ).to_numpy(dtype=bool)

        if supply_settings is not None:
            fuel_f = _province_ref_fuel_eur_from_seg0_network(
                n,
                p,
                generator_carriers=generator_carriers,
                link_carrier_to_bus1_carrier=link_carrier_to_bus1_carrier,
            )
            if fuel_f is None or fuel_f <= 0.0:
                fuel_f = _province_ref_fuel_eur_from_blocks(blocks_full.get(p, []) or [])

            if fuel_f is not None and float(fuel_f) > 0.0:
                mult_f = _mapped_multiplier_from_lr_normalized(
                    lr_full[p].to_numpy(dtype=float), supply_settings
                )
                vals = (mult_f * float(fuel_f)).astype(float)
                vals[zero_mask] = 0.0
                out[p] = vals
                continue

        if cfg_curve is not None:
            x, y = cfg_curve
            vals = np.interp(lr_full[p].to_numpy(dtype=float), x, y).astype(float)
            vals[zero_mask] = 0.0
            out[p] = vals
            continue

        xf, yf = _build_interp_curve(blocks_full.get(p, []))
        vals = np.interp(lr_full[p].to_numpy(dtype=float), xf, yf).astype(float)
        vals[zero_mask] = 0.0
        out[p] = vals

    return out.fillna(0.0).clip(lower=0.0)


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
    coal_1x = _province_coal_power_1x_prices(
        n,
        provinces=pd.Index(local.columns),
        snapshots=pd.Index(local.index),
    )
    zero_price_mask = local.astype(float) <= 1e-9
    local_mapped = local.copy()
    floor_enabled, floor_ratio = _load_thermal_load_floor_config(config_path=config_path)
    if floor_enabled:
        thermal, _, _ = _weekly_lr_and_blocks(
            n,
            provinces=pd.Index(local.columns),
            snapshots=pd.Index(local.index),
            week_freq=str(week_freq),
            generator_carriers=generator_carriers,
            link_carrier_to_bus1_carrier=link_carrier_to_bus1_carrier,
            config_path=config_path,
        )
        near_floor_mask = _thermal_load_floor_band_mask(
            thermal,
            n,
            provinces=pd.Index(local.columns),
            snapshots=pd.Index(local.index),
            ratio=float(floor_ratio),
            multiplier=1.5,
        )
        zero_price_mask = zero_price_mask | near_floor_mask
        local_mapped = local_mapped.mask(near_floor_mask, 0.0)
    out = local_mapped
    out = out.mask((out < coal_1x) & ~zero_price_mask, coal_1x)
    out = out.mask(zero_price_mask, 0.0)
    return out.fillna(0.0).clip(lower=0.0)


def _infer_planning_network_path(dispatch_network_path: str | Path) -> Path | None:
    path = Path(dispatch_network_path)
    parts = list(path.parts)
    if "dispatch_segmented" not in parts:
        return None
    idx = parts.index("dispatch_segmented")
    if idx + 1 >= len(parts):
        return None
    heating_demand = parts[idx + 1]
    name = path.name
    prefix = "postnetwork-dispatch-seg-"
    if not name.startswith(prefix):
        return None
    planning_name = f"postnetwork-{name[len(prefix):]}"
    candidate = Path(*parts[:idx]) / "postnetworks" / heating_demand / planning_name
    return candidate


def _planning_marginal_floor_zero_mapped_prices(
    n_dispatch: pypsa.Network,
    n_planning: pypsa.Network,
    *,
    week_freq: str,
    config_path: str | Path | None = None,
) -> pd.DataFrame:
    provinces = _province_elec_buses(n_dispatch)
    snapshots = pd.Index(n_dispatch.snapshots)
    prices = marginal_retail_prices(n_planning, config=ReconstructPriceConfig(week_freq=week_freq))
    prices = prices.reindex(index=snapshots, columns=list(map(str, provinces))).fillna(0.0).astype(float)

    generator_carriers, link_carrier_to_bus1_carrier = _load_mapped_carrier_config(config_path=config_path)
    thermal_output = _infer_local_thermal_dispatch(
        n_dispatch,
        provinces=provinces,
        snapshots=snapshots,
        generator_carriers=generator_carriers,
        link_carrier_to_bus1_carrier=link_carrier_to_bus1_carrier,
    )
    low_output_thermal = thermal_output

    floor_mask = pd.DataFrame(False, index=prices.index, columns=prices.columns, dtype=bool)

    apply_sync_zero = _load_apply_synchronous_generation_floor_zero_mask(config_path=config_path)
    (
        floor_enabled,
        floor_ratio,
        sync_generator_carriers,
        sync_link_carrier_to_bus1_carrier,
        floor_slack_mw,
        apply_start_year,
        apply_end_year,
    ) = _load_synchronous_generation_floor_config(config_path=config_path)
    if apply_sync_zero and floor_enabled:
        sync_output = _infer_local_thermal_dispatch(
            n_dispatch,
            provinces=provinces,
            snapshots=snapshots,
            generator_carriers=sync_generator_carriers,
            link_carrier_to_bus1_carrier=sync_link_carrier_to_bus1_carrier,
        )
        load_mw = _province_ac_load(n_dispatch, provinces, snapshots)
        floor_mask = _sync_generation_floor_zero_mask(
            sync_output,
            load_mw,
            ratio=floor_ratio,
            rhs_slack_mw=floor_slack_mw,
            zero_band_mw=_load_sync_floor_zero_band_mw(config_path=config_path),
            apply_start_year=apply_start_year,
            apply_end_year=apply_end_year,
        )
    if (
        _load_low_output_carrier_scope(config_path=config_path) == "synchronous_generation_floor"
        and floor_enabled
    ):
        low_output_thermal = _infer_local_thermal_dispatch(
            n_dispatch,
            provinces=provinces,
            snapshots=snapshots,
            generator_carriers=sync_generator_carriers,
            link_carrier_to_bus1_carrier=sync_link_carrier_to_bus1_carrier,
        )

    zero_threshold = _daily_low_output_zero_threshold(low_output_thermal.index, config_path=config_path)
    low_output_freq = _low_output_reference_freq(config_path=config_path)
    low_output_reserve_margin = _low_output_reserve_margin(config_path=config_path)
    for province in low_output_thermal.columns:
        low_output_mask = _daily_low_output_zero_mask(
            low_output_thermal[province].astype(float),
            threshold=zero_threshold,
            freq=low_output_freq,
            reserve_margin=low_output_reserve_margin,
        )
        floor_mask[province] = floor_mask[province] | low_output_mask

    floor_mask = floor_mask.reindex(index=prices.index, columns=prices.columns).fillna(False)
    if not floor_mask.any().any():
        return prices.fillna(0.0).clip(lower=0.0)
    return prices.mask(floor_mask, 0.0).fillna(0.0).clip(lower=0.0)


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
    mapped_price_source: str | None = None,
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

    mapped_source = (
        str(mapped_price_source).strip().lower()
        if mapped_price_source
        else _load_mapped_price_source_config(config_path=config_path)
    )
    if mapped_source in {"planning", "planning_marginal", "planning_with_floor_zero"}:
        mapped_source = "planning_marginal_floor_zero"
    if mapped_source not in {"reconstructed", "planning_marginal_floor_zero"}:
        raise ValueError("mapped_price_source must be 'reconstructed' or 'planning_marginal_floor_zero'")

    if mapped_source == "planning_marginal_floor_zero":
        planning_path = Path(baseline_network_path) if baseline_network_path else _infer_planning_network_path(network_path)
        if planning_path is None or not planning_path.exists():
            raise FileNotFoundError(
                "mapped_price_source='planning_marginal_floor_zero' requires a planning postnetwork. "
                "Pass --baseline-network or use the standard dispatch/postnetwork path layout."
            )
        n_planning_for_mapped = pypsa.Network(planning_path)
        mapped_prices = _planning_marginal_floor_zero_mapped_prices(
            n,
            n_planning_for_mapped,
            week_freq=week_freq,
            config_path=config_path,
        )
    else:
        # Sidecar output: mapped prices reconstructed from dispatch result.
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
        if mapped_source == "reconstructed":
            mapped_baseline = _province_coal_power_1x_prices(
                n,
                provinces=pd.Index(mapped_prices.columns),
                snapshots=pd.Index(mapped_prices.index),
            ).reindex(index=mapped_prices.index, columns=mapped_prices.columns).fillna(0.0).astype(float)
            mapped_f = mapped_prices.astype(float)
            mapped_zero_mask = mapped_f <= 1e-9
            mapped_prices = mapped_f.mask((mapped_f < mapped_baseline) & ~mapped_zero_mask, mapped_baseline)
            mapped_prices = mapped_prices.mask(mapped_zero_mask, 0.0)
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
        default="W-SUN",
        help=(
            "Pandas offset alias for weekly buckets used when normalizing thermal load ratio: "
            "each snapshot is divided by the maximum (thermal/cap) in its week "
            "(default W-SUN; match config dispatch_segmented_prices.price_export.week_freq)."
        ),
    )
    ap.add_argument(
        "--import-agg",
        default="min_offer",
        choices=["min_offer", "max_offer"],
        help=(
            "Deprecated compatibility argument. "
            "Mapped sidecar prices are now mapped independently by province."
        ),
    )
    ap.add_argument(
        "--line-cong-eps-mw",
        type=float,
        default=1e-3,
        help="Deprecated compatibility argument; mapped sidecar ignores line-flow adjustment.",
    )
    ap.add_argument(
        "--min-inflow-mw",
        type=float,
        default=1e-3,
        help="Deprecated compatibility argument; mapped sidecar ignores line-flow adjustment.",
    )
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
            "baseline LMPs (--baseline-network). Mapped sidecar uses 1x coal-power "
            "generation cost as its floor instead of baseline LMPs."
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
        "--mapped-price-source",
        default=None,
        choices=["reconstructed", "planning_marginal_floor_zero"],
        help=(
            "Mapped sidecar source. Default comes from "
            "dispatch_segmented_prices.price_export.mapped_price_source."
        ),
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
        mapped_price_source=(str(args.mapped_price_source) if args.mapped_price_source else None),
    )


if __name__ == "__main__":
    main()
