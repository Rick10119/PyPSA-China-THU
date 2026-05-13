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
import yaml

import matplotlib.pyplot as plt
import pypsa

import sys

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from reconstruct_market_prices import ReconstructPriceConfig, marginal_retail_prices  # noqa: E402


def _default_config_path() -> Path:
    return _THIS_DIR.parent / "config.yaml"


def _load_mapped_carrier_config(config_path: str | Path | None = None) -> tuple[set[str], dict[str, str]]:
    """
    Load carrier filters for mapped-price reconstruction from config:
    dispatch_segmented_prices.mapped_carriers.{Generator,Link}
    (fallback: dispatch_segmented_prices.carriers.{Generator,Link}).
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
    link_carrier_to_bus1_carrier: dict[str, str] = {}
    for k, v in link_cfg.items():
        c = str(k)
        only_bus1 = ""
        if isinstance(v, dict):
            only_bus1 = str(v.get("only_bus1_carrier", "") or "")
        link_carrier_to_bus1_carrier[c] = only_bus1
    return generator_carriers, link_carrier_to_bus1_carrier


def _resolve_bus_province(bus_name: str) -> str:
    return str(bus_name).split(" ", 1)[0]


def _generator_selected(carrier: str, generator_carriers: set[str]) -> bool:
    return str(carrier) in generator_carriers


def _link_selected(n: pypsa.Network, row: pd.Series, link_carrier_to_bus1_carrier: dict[str, str]) -> bool:
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


def _shandong_thermal_dispatch(
    n: pypsa.Network,
    snapshots: pd.Index,
    province: str,
    *,
    generator_carriers: set[str],
    link_carrier_to_bus1_carrier: dict[str, str],
) -> pd.Series:
    """
    Sum mapped thermal output (Generator + Link electric injection) for one province.

    This keeps the plot consistent with mapped-price carrier selection:
    - Generator carriers from mapped config
    - Link carriers from mapped config, using injection at bus1 (-p1)
    """
    out = pd.Series(0.0, index=snapshots, name="thermal_dispatch_MW", dtype=float)
    province = str(province)

    if (
        hasattr(n, "generators")
        and not n.generators.empty
        and hasattr(n, "generators_t")
        and hasattr(n.generators_t, "p")
    ):
        gen = n.generators
        gmask = gen["carrier"].astype(str).map(lambda c: _generator_selected(c, generator_carriers))
        gidx = gen.index[gmask]
        if len(gidx):
            gp = n.generators_t.p.reindex(index=snapshots, columns=gidx).fillna(0.0)
            for g in gidx:
                p = _resolve_bus_province(str(gen.at[g, "bus"]))
                if p == province:
                    out = out.add(pd.to_numeric(gp[g], errors="coerce").fillna(0.0).clip(lower=0.0), fill_value=0.0)

    if hasattr(n, "links") and not n.links.empty and hasattr(n, "links_t") and hasattr(n.links_t, "p1"):
        links = n.links
        lmask = links.apply(lambda r: _link_selected(n, r, link_carrier_to_bus1_carrier), axis=1)
        lidx = links.index[lmask]
        if len(lidx):
            p1 = n.links_t.p1.reindex(index=snapshots, columns=lidx).fillna(0.0)
            for l in lidx:
                p = _resolve_bus_province(str(links.at[l, "bus1"]))
                if p == province:
                    inj = (-pd.to_numeric(p1[l], errors="coerce").fillna(0.0)).clip(lower=0.0)
                    out = out.add(inj, fill_value=0.0)

    out = out.fillna(0.0)
    out.name = "thermal_dispatch_MW"
    return out


def export_price_vs_thermal_plots(
    *,
    n: pypsa.Network,
    out_prefix: str | Path,
    province: str = "Shandong",
    week_freq: str = "W-SUN",
    sample: int = 0,
    price_mode: str = "marginal",
    currency: str = "CNY",
    fx_cny_per_eur: float = 7.8,
    config_path: str | Path | None = None,
    price_series: pd.Series | None = None,
    price_label: str | None = None,
) -> None:
    if price_series is None:
        cfg = ReconstructPriceConfig(week_freq=str(week_freq))
        prices = marginal_retail_prices(n, config=cfg)
        if province not in prices.columns:
            raise ValueError(f"Province '{province}' not found in reconstructed prices columns.")
        price = prices[province].copy()
        y_label = str(price_label) if price_label else "Marginal price"
        cur = str(currency).upper()
        if cur in {"CNY", "RMB"}:
            price = price.astype(float) * float(fx_cny_per_eur)
            y_unit = "CNY/MWh"
        else:
            price = price.astype(float)
            y_unit = "EUR/MWh"
    else:
        price = pd.to_numeric(price_series, errors="coerce").astype(float)
        y_label = str(price_label) if price_label else "Mapped price"
        cur = str(currency).upper()
        if cur in {"CNY", "RMB"}:
            y_unit = "CNY/MWh"
        elif cur in {"EUR"}:
            y_unit = "EUR/MWh"
        else:
            raise ValueError("currency must be EUR or CNY (RMB accepted as alias)")

    price.index = pd.to_datetime(price.index)

    generator_carriers, link_carrier_to_bus1_carrier = _load_mapped_carrier_config(config_path=config_path)
    th = _shandong_thermal_dispatch(
        n,
        price.index,
        province,
        generator_carriers=generator_carriers,
        link_carrier_to_bus1_carrier=link_carrier_to_bus1_carrier,
    )
    th = th.rename("thermal_dispatch_MW")
    th.index = pd.to_datetime(th.index)

    df = pd.concat([price.rename("price"), th], axis=1).dropna()

    out_prefix = Path(out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    # Optional downsample for scatter clarity
    if sample and sample > 0 and len(df) > sample:
        df_sc = df.sample(n=int(sample), random_state=0)
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
    title_mode = str(price_mode)
    if price_series is not None and price_label:
        title_mode = str(price_label).lower().replace(" ", "_")
    plt.title(f"{province}: full-year {title_mode} price vs thermal dispatch")
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

    plt.title(f"{province}: full-year {title_mode} price and thermal dispatch")
    plt.tight_layout()
    plt.savefig(out_prefix.with_suffix(".timeseries.png"), dpi=180)

    # 3) Export the paired data
    df.to_csv(out_prefix.with_suffix(".csv"), index_label="snapshot")


def export_seasonal_random_day_profiles(
    *,
    n: pypsa.Network,
    out_prefix: str | Path,
    province: str = "Shandong",
    currency: str = "CNY",
    config_path: str | Path | None = None,
    price_series: pd.Series | None = None,
    price_label: str | None = None,
    random_state: int | None = 42,
) -> None:
    """
    For each meteorological season, pick one random calendar day present in snapshots
    and plot that day's mapped price vs thermal dispatch (dual axis).

    Seasons (Northern Hemisphere): Spring MAM, Summer JJA, Autumn SON, Winter DJF.
    """
    if price_series is None:
        raise ValueError("price_series is required for seasonal random-day plots.")
    province = str(province)
    price = pd.to_numeric(price_series, errors="coerce").astype(float)
    price.index = pd.to_datetime(price.index)
    y_label = str(price_label) if price_label else "Mapped price"
    cur = str(currency).upper()
    if cur in {"CNY", "RMB"}:
        y_unit = "CNY/MWh"
    elif cur in {"EUR"}:
        y_unit = "EUR/MWh"
    else:
        raise ValueError("currency must be EUR or CNY (RMB accepted as alias)")

    generator_carriers, link_carrier_to_bus1_carrier = _load_mapped_carrier_config(config_path=config_path)
    th = _shandong_thermal_dispatch(
        n,
        price.index,
        province,
        generator_carriers=generator_carriers,
        link_carrier_to_bus1_carrier=link_carrier_to_bus1_carrier,
    )
    th.index = pd.to_datetime(th.index)
    df = pd.concat([price.rename("price"), th.rename("thermal_dispatch_MW")], axis=1).dropna()
    if df.empty:
        raise ValueError("No overlapping mapped price / thermal snapshots for seasonal plots.")

    out_prefix = Path(out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(random_state)

    season_defs: tuple[tuple[str, list[int]], ...] = (
        ("Spring (MAM)", [3, 4, 5]),
        ("Summer (JJA)", [6, 7, 8]),
        ("Autumn (SON)", [9, 10, 11]),
        ("Winter (DJF)", [12, 1, 2]),
    )

    ready_by_season: dict[str, tuple[pd.DatetimeIndex, pd.Series, pd.Series]] = {}
    for season_name, months in season_defs:
        day_candidates = sorted(
            {ts.normalize().to_pydatetime() for ts in df.index if int(ts.month) in months}
        )
        if not day_candidates:
            continue
        pick = pd.Timestamp(day_candidates[int(rng.integers(len(day_candidates)))])
        rows = df[df.index.normalize() == pick]
        if rows.empty:
            continue
        ready_by_season[season_name] = (
            rows.index,
            rows["price"],
            rows["thermal_dispatch_MW"],
        )

    if not ready_by_season:
        raise ValueError("No snapshots fall into any meteorological season; cannot build seasonal plots.")

    fig, axs = plt.subplots(2, 2, figsize=(11, 8.0))
    flat_axes = axs.flatten()
    order = [sd[0] for sd in season_defs]

    for i, sea in enumerate(order):
        ax = flat_axes[i]
        if sea not in ready_by_season:
            ax.axis("off")
            continue
        ix, pr, thd = ready_by_season[sea]
        hours = ix.hour + ix.minute / 60.0 + ix.second / 3600.0
        (ln1,) = ax.plot(hours, pr.to_numpy(), color="#d62728", linewidth=1.4, label="mapped price")
        ax.set_ylabel(f"{y_label} [{y_unit}]", color="#d62728")
        ax.tick_params(axis="y", labelcolor="#d62728")
        ax.set_xlabel("Hour of day")
        ax.grid(True, alpha=0.25)
        ax_top = ix[0].strftime("%Y-%m-%d")
        ax.set_title(f"{province} · {sea}\nrandom day: {ax_top}")

        ax2 = ax.twinx()
        (ln2,) = ax2.plot(
            hours,
            thd.to_numpy(),
            color="#1f77b4",
            linewidth=1.2,
            alpha=0.9,
            label="thermal",
        )
        ax2.set_ylabel("Thermal dispatch (coal+gas) [MW]", color="#1f77b4")
        ax2.tick_params(axis="y", labelcolor="#1f77b4")
        ax.legend([ln1, ln2], ["mapped price", "thermal MW"], loc="upper right", fontsize=8)

    plt.suptitle(f"{province}: one random day per season — mapped price and thermal dispatch", y=1.02, fontsize=12)
    plt.tight_layout()
    seasonal_path = Path(str(out_prefix) + ".seasonal_random_days.png")
    plt.savefig(seasonal_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


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
    ap.add_argument("--config", default=None, help="Optional config.yaml path used for mapped carrier selection.")
    args = ap.parse_args()
    n = pypsa.Network(args.network)
    export_price_vs_thermal_plots(
        n=n,
        out_prefix=args.out_prefix,
        province=args.province,
        week_freq=str(args.week_freq),
        sample=int(args.sample),
        price_mode=str(args.price_mode),
        currency=str(args.currency),
        fx_cny_per_eur=float(args.fx_cny_per_eur),
        config_path=(str(args.config) if args.config else None),
    )


if __name__ == "__main__":
    main()

